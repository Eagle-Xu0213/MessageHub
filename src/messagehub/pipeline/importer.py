"""统一导入管线：解析 → 去重 → 入库。

设计目标（来自开发交接书 §4）：
- 去重优先平台稳定消息 ID；无 ID 则用指纹（平台+会话+发送者+时间窗+正文）。
- 同一文件导入三次不得膨胀。
- 畸形输入不破坏原数据库（逐行 try/except + 事务）。
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from messagehub.adapters.registry import detect_and_parse


def make_fingerprint(m: dict) -> str:
    """稳定指纹：优先 source_message_id；否则内容级指纹。"""
    sid = m.get("source_message_id")
    if sid:
        return f"id::{m.get('source_platform','?')}::{sid}"
    ts = (m.get("sent_at") or "")[:17]  # 精确到分钟，分钟级重复视为同一条
    key = "|".join([
        str(m.get("source_platform", "unknown")),
        str(m.get("conversation_id", "unknown")),
        str(m.get("sender_name", "unknown")),
        ts,
        str(m.get("text", "")),
    ])
    return "fp::" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def insert_messages(conn: sqlite3.Connection, raw_list: list[dict]) -> dict:
    """批量插入（事务），返回统计。已存在指纹跳过。"""
    inserted = 0
    duplicate = 0
    skipped = 0
    cur = conn.cursor()
    try:
        for m in raw_list:
            if str(m.get("source_platform", "")).lower() == "qq":
                skipped += 1
                continue
            fp = make_fingerprint(m)
            cur.execute("SELECT 1 FROM messages WHERE fingerprint = ?", (fp,))
            if cur.fetchone():
                duplicate += 1
                continue
            cur.execute(
                """INSERT INTO messages
                   (source_platform, source_method, account_ref, conversation_id,
                    conversation_name, sender_name, sent_at, text, message_type,
                    source_message_id, fingerprint, time_confidence, capture_status, sender_ref)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    m.get("source_platform", "unknown"),
                    m.get("source_method", "file_import"),
                    m.get("account_ref"),
                    m.get("conversation_id") or "unknown",
                    m.get("conversation_name") or "unknown",
                    m.get("sender_name") or "unknown",
                    m.get("sent_at") or "",
                    m.get("text", ""),
                    m.get("message_type") or "text",
                    m.get("source_message_id"),
                    fp,
                    m.get("time_confidence") or ("exact" if m.get("sent_at") else "unknown"),
                    m.get("capture_status") or "full",
                    m.get("sender_ref"),
                ),
            )
            inserted += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"inserted": inserted, "duplicate": duplicate, "skipped": skipped,
            "total": len(raw_list)}


def import_file(db_path: str, path: str, batch_name: Optional[str] = None) -> dict:
    """解析单个文件并入库。返回 (raw, stats)。失败不影响其他文件。"""
    raw, platform_hint = detect_and_parse(path)
    if not raw:
        return {"raw": [], "stats": {"inserted": 0, "duplicate": 0, "skipped": 0, "total": 0},
                "platform_hint": platform_hint, "error": "no parseable messages"}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        stats = insert_messages(conn, raw)
    finally:
        conn.close()
    return {"raw": raw, "stats": stats, "platform_hint": platform_hint, "error": None}
