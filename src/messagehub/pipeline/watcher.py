"""目录扫描器：自动监测导出文件夹，增量导入并去重，记录采集状态。

四种采集状态（开发交接书 §9 / 用户要求）：
- ok_no_new   : 成功读取，没有新消息
- ok_new      : 成功读取，有新增
- partial     : 数据不完整（解析出部分消息）
- error       : 读取失败
- not_started : 尚未开始读取（初始/未配置）

不读取原始微信/QQ数据库，只处理用户已合法导出的文件。
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Optional

from messagehub.pipeline.importer import import_file, make_fingerprint

WATCH_EXTS = (".json", ".jsonl", ".csv", ".txt")


def _file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _record_scan_state(conn: sqlite3.Connection, path: str, row_count: int):
    cur = conn.cursor()
    mtime = os.path.getmtime(path)
    size = os.path.getsize(path)
    fhash = _file_hash(path)
    cur.execute(
        """INSERT INTO scan_state (file_path, content_hash, mtime, size, parsed_rows, last_scanned)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(file_path) DO UPDATE SET
             content_hash=excluded.content_hash, mtime=excluded.mtime,
             size=excluded.size, parsed_rows=excluded.parsed_rows,
             last_scanned=excluded.last_scanned""",
        (path, fhash, mtime, size, row_count, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def _already_scanned(conn: sqlite3.Connection, path: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT content_hash, mtime, size FROM scan_state WHERE file_path=?", (path,))
    row = cur.fetchone()
    if not row:
        return False
    if abs(row[1] - os.path.getmtime(path)) > 1.0 or row[2] != os.path.getsize(path):
        return False  # 文件已变更
    return True


def scan_directory(db_path: str, watch_dir: str, recursive: bool = True,
                   source_label: str = "auto_scan") -> dict:
    """扫描目录，增量导入新文件/变更文件。返回本轮采集汇总。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    started = datetime.now(timezone.utc).isoformat()
    run_status = "ok_no_new"
    total_new = 0
    total_dup = 0
    errors = []
    files_seen = 0
    files_skipped = 0

    try:
        if recursive:
            walker = (os.path.join(root, f) for root, _, files in os.walk(watch_dir) for f in files)
        else:
            walker = (os.path.join(watch_dir, f) for f in os.listdir(watch_dir)
                      if os.path.isfile(os.path.join(watch_dir, f)))
        for path in walker:
            if not path.lower().endswith(WATCH_EXTS):
                continue
            files_seen += 1
            if _already_scanned(conn, path):
                files_skipped += 1
                continue
            try:
                result = import_file(db_path, path, batch_name=os.path.basename(path))
                stats = result["stats"]
                total_new += stats["inserted"]
                total_dup += stats["duplicate"]
                _record_scan_state(conn, path, stats["total"])
                if stats["inserted"] > 0:
                    run_status = "ok_new"
                if result.get("error"):
                    # 文件可访问但无法解析为消息 → 明确报错，不假装无新消息
                    errors.append(f"{os.path.basename(path)}: {result['error']}")
                    run_status = "error"
            except Exception as e:
                errors.append(f"{os.path.basename(path)}: {e}")
                run_status = "error"
                # 仍记录扫描状态，避免反复重试损坏文件；但允许下次变更重试
                try:
                    _record_scan_state(conn, path, 0)
                except Exception:
                    pass

        if errors:
            run_status = "error"
    finally:
        finished = datetime.now(timezone.utc).isoformat()
        cur.execute(
            """INSERT INTO capture_runs
               (source_label, source_path, started_at, finished_at, status, new_count, dup_count, error, note)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (source_label, watch_dir, started, finished, run_status,
             total_new, total_dup,
             "\n".join(errors) if errors else None,
             f"扫描文件 {files_seen} 个，跳过已处理 {files_skipped} 个"),
        )
        conn.commit()
        conn.close()

    return {
        "status": run_status,
        "files_seen": files_seen,
        "files_skipped": files_skipped,
        "new": total_new,
        "duplicate": total_dup,
        "errors": errors,
    }


def get_latest_capture_status(db_path: str) -> list[dict]:
    """读取最近采集运行状态（供 UI 展示）。"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM capture_runs ORDER BY id DESC LIMIT 20")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows
