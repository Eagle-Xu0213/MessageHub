"""每日工作简报生成（R2/R3.1）：事件三栏聚合 → LLM 摘要 → 落库。

三栏定义（与用户确认的规则一致）：
  todos      今日待办——未答复的 assignment（闭环状态为 answered_* 的不再报）
  replies    需你回复——未答复的 reply_needed
  notices    通知——全部 notice（不需要个人答复，但要知道）
闭环事件（已应答/已改期/已婉拒）进 resolved 栏，供「今日进展」回顾。

基线语义（用户 2026-11-17 定：历史清零、只看新消息）：
  runtime/brief_baseline.json 保存基线时间戳；collect_events 只提取
  sent_at >= 基线 的消息产生的事件。设一次基线，10 月的陈年旧事
  永久退出视野；R4 定时任务每天生成简报后推进基线，实现滚动监测。

输出简报结构（briefs.summary JSON）：
{
  "date": "2026-11-17",
  "sections": {"todos": [...], "replies": [...], "notices": [...]},
  "resolved": [...],
  "ai_summary": "一段人话总结（LLM 失败时为规则版）",
  "stats": {"total": N, "open": N}
}
每条事件保留 message_id 供前端溯源到原始消息。
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from typing import Any, Dict, List, Optional

from messagehub import personal
from messagehub.config import BASE_DIR, DB_PATH, CN_TZ, now_cn_iso
from messagehub.database import get_db
from messagehub.rules import extract_events
from messagehub.resolution import enrich_events_with_resolution

RESOLVED_STATES = {"answered_ok", "answered_alt", "answered_no"}

BASELINE_FILE = BASE_DIR / "runtime" / "brief_baseline.json"


# ---------------- 基线管理 ----------------

def get_baseline() -> Optional[str]:
    """读取事件提取基线（ISO 时间戳）；未设置返回 None。"""
    try:
        data = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
        ts = data.get("baseline")
        return str(ts) if ts else None
    except (OSError, ValueError):
        return None


def set_baseline(ts: Optional[str] = None) -> str:
    """设置基线（默认当前北京时间）。返回实际写入的时间戳。"""
    ts = ts or now_cn_iso()
    BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_FILE.write_text(
        json.dumps({"baseline": ts, "set_at": now_cn_iso()}, ensure_ascii=False),
        encoding="utf-8")
    return ts


def clear_baseline() -> None:
    """清除基线（回到按天数窗口的历史模式）。"""
    try:
        BASELINE_FILE.unlink()
    except OSError:
        pass


# ---------------- 事件提取入口 ----------------

def collect_events(db_path: str = None, days: int = 1,
                   baseline: Optional[str] = None) -> List[Dict[str, Any]]:
    """提取工作事件并做闭环分析。

    基线优先：baseline 参数或 brief_baseline.json 任一存在时，只取该时刻
    之后的消息（用户定的"清零重开"语义）；否则退回 days 天窗口。
    闭环分析的上下文用全量消息（事件前后文可能跨越基线）。
    """
    conn = sqlite3.connect(str(db_path or DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, sent_at, conversation_name, sender_name, text "
            "FROM messages WHERE sent_at LIKE '20%' "
            "ORDER BY sent_at ASC, id ASC"
        ).fetchall()
    finally:
        conn.close()
    msgs = [dict(r) for r in rows]

    # 基线语义：baseline 参数显式传入（含空串=强制不用基线）优先；
    # 未传参数时回退到基线文件。base="" → 走 days 窗口（管理/样例用途）。
    if baseline is not None:
        base = baseline or None
    else:
        base = get_baseline()
    if base:
        msgs_in = [m for m in msgs if (m.get("sent_at") or "") >= base]
    elif days and days > 0 and msgs:
        import datetime as _dt
        latest = max((m.get("sent_at") or "")[:10] for m in msgs)
        try:
            anchor = _dt.date.fromisoformat(latest)
        except ValueError:
            anchor = _dt.date.fromisoformat(now_cn_iso()[:10])
        cutoff_str = (anchor - _dt.timedelta(days=days - 1)).isoformat()
        msgs_in = [m for m in msgs if (m.get("sent_at") or "")[:10] >= cutoff_str]
    else:
        msgs_in = msgs

    events = extract_events(msgs_in)
    # 闭环分析用全量消息做上下文（事件的前后文可跨越基线）
    events = enrich_events_with_resolution(events, msgs)
    return events


# ---------------- 三栏聚合 ----------------

def _ev_view(e: Dict[str, Any]) -> Dict[str, Any]:
    """事件的前端视图：保留溯源字段，裁掉大文本。"""
    return {
        "message_id": e.get("message_id"),
        "sent_at": e.get("sent_at"),
        "group": e.get("conversation_name"),
        "sender": e.get("sender_name"),
        "text": (e.get("text") or "")[:200],
        "event_type": e.get("event_type"),
        "deadline": e.get("deadline"),
        "reply_status": e.get("reply_status"),
        "reply_evidence": e.get("reply_evidence"),
    }


def build_sections(events: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    todos, replies, notices, resolved = [], [], [], []
    for e in events:
        st = e.get("reply_status")
        if st in RESOLVED_STATES:
            resolved.append(_ev_view(e))
            continue
        t = e.get("event_type")
        if t == "assignment":
            todos.append(_ev_view(e))
        elif t == "reply_needed":
            replies.append(_ev_view(e))
        elif t == "notice":
            notices.append(_ev_view(e))
    return {"todos": todos, "replies": replies, "notices": notices,
            "resolved": resolved}


# ---------------- 摘要 ----------------

def _rule_summary(sections: Dict[str, List[Dict[str, Any]]], date: str) -> str:
    """LLM 不可用时的规则版摘要（始终可用）。"""
    n_todo = len(sections["todos"])
    n_reply = len(sections["replies"])
    n_notice = len(sections["notices"])
    n_done = len(sections["resolved"])
    parts = []
    if n_todo:
        tops = [f"「{e['text'][:24]}…」({e['group']})" for e in sections["todos"][:3]]
        parts.append(f"有 {n_todo} 件待办，如 {'、'.join(tops)}")
    if n_reply:
        parts.append(f"{n_reply} 条消息等你回复")
    if n_notice:
        parts.append(f"{n_notice} 条群通知（答辩/值班/截止类）")
    if n_done:
        parts.append(f"另有 {n_done} 件你已处理过")
    if not parts:
        return f"{date} 目前没有新工作事件，监测中。"
    return f"{date}：" + "；".join(parts) + "。"


def _ai_summary(sections: Dict[str, List[Dict[str, Any]]], date: str) -> Dict[str, Any]:
    """尝试 LLM 生成人话摘要；失败返回规则版。返回 {text, degraded}。"""
    from messagehub.llm.provider import LLMProvider
    provider = LLMProvider.from_env()
    if provider.enabled:
        lines = []
        for key, label in (("todos", "待办"), ("replies", "需回复"), ("notices", "通知")):
            for e in sections[key]:
                lines.append(f"[{label}] {e['group']} | {e['sender']} | "
                             f"{e['text'][:80]} | 截止:{e['deadline'] or '无'}")
        prompt = (
            "把下面这些工作事件整理成晨报。返回严格 JSON，格式："
            '{"summary": "150字以内的中文晨报正文"}。\n'
            "要求：面向" + personal.BRIEF_PERSONA + "，语气自然不官腔，先讲需要他动手的，"
            "再讲通知。不要编造事实，只用给出的事件。\n"
            "如果没有任何事件，summary 写：「今天暂无工作事件，监测中。」\n"
            f"日期：{date}\n" + "\n".join(lines[:40])
        )
        data = provider.chat_json(prompt, timeout=30)
        if data and isinstance(data.get("summary"), str) and data["summary"].strip():
            return {"text": data["summary"].strip(), "degraded": False}
    return {"text": _rule_summary(sections, date), "degraded": True}


# ---------------- 落库 ----------------

def _ensure_briefs_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS briefs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE NOT NULL,
            generated_at TEXT NOT NULL,
            summary TEXT NOT NULL,
            event_count INTEGER DEFAULT 0,
            degraded INTEGER DEFAULT 0
        )
    """)


def save_brief(brief: Dict[str, Any], db_path: str = None) -> int:
    """写入/更新当日简报（date 唯一，重跑覆盖）。返回 brief id。"""
    conn = sqlite3.connect(str(db_path or DB_PATH))
    try:
        _ensure_briefs_table(conn)
        conn.execute(
            "INSERT INTO briefs (date, generated_at, summary, event_count, degraded) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(date) DO UPDATE SET generated_at=excluded.generated_at, "
            "summary=excluded.summary, event_count=excluded.event_count, "
            "degraded=excluded.degraded",
            (brief["date"], now_cn_iso(), json.dumps(brief, ensure_ascii=False),
             brief["stats"]["total"], 1 if brief["degraded"] else 0))
        conn.commit()
        row = conn.execute("SELECT id FROM briefs WHERE date=?", (brief["date"],)).fetchone()
        return row[0]
    finally:
        conn.close()


# ---------------- 主入口 ----------------

def generate_brief(days: int = 1, date: str = None, save: bool = True,
                   db_path: str = None, baseline: Optional[str] = None,
                   keep_baseline: bool = True) -> Dict[str, Any]:
    """生成简报。

    基线模式（默认）：baseline 参数或 brief_baseline.json 存在时，只提取
    基线之后的新消息事件——"历史清零、滚动监测"语义（用户 2026-11-17 定）。
    无基线时退回 days 天窗口（测试兼容）。

    keep_baseline=False：一次性清空基线后按 days 窗口生成（管理用途）。
    """
    if date is None:
        date = now_cn_iso()[:10]
    if not keep_baseline:
        clear_baseline()
    events = collect_events(db_path=db_path, days=days, baseline=baseline)
    sections = build_sections(events)
    ai = _ai_summary(sections, date)
    brief = {
        "date": date,
        "baseline": baseline or get_baseline(),
        "sections": sections,
        "ai_summary": ai["text"],
        "degraded": ai["degraded"],
        "stats": {
            "total": len(events),
            "todos": len(sections["todos"]),
            "replies": len(sections["replies"]),
            "notices": len(sections["notices"]),
            "resolved": len(sections["resolved"]),
        },
        "generated_at": now_cn_iso(),
    }
    if save:
        brief["brief_id"] = save_brief(brief, db_path=db_path)
    return brief


def reset_and_set_baseline(ts: str = None) -> str:
    """清零重开：设定新基线并清空旧简报存档。返回基线时间戳。

    用户语义：从现在开始所有东西清零，只监测新收到的消息。
    """
    ts = set_baseline(ts)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute("DELETE FROM briefs")
        conn.commit()
    finally:
        conn.close()
    return ts
