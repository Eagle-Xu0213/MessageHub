"""Incremental, conservative personal task classification.

This module is deliberately independent of the legacy digest and never reads
or writes production data until called with an explicit SQLite connection.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from messagehub import personal
from messagehub.config import CN_TZ

_ACTION = re.compile(r"提交|上交|填写|报名|回复|确认|发送|发给|发我|交给|整理|准备|完成|处理|跟进|核对|签字|盖章|参加|出席|写|制作|做|领取|上传|请.{0,8}交")
_REPLY_REQUEST = re.compile(r"(?:能|是否|可不可以|能不能|有没有|行不行|可以).{0,20}(?:吗|么|\?|？)|请.{0,8}(?:回复|确认)")
_WORK = re.compile(r"提交|材料|表格|报名|回复|确认|会议|开会|任务|截止|通知|工作|报告|安排|文件|负责|处理|跟进|填写|上传|完成|准备|做|写|请|麻烦|辛苦")
_DONE = re.compile(r"(?:已经|已|我)(?:经)?(?:完成|提交|交了|发了|发送|上传|填好|写好|做好|处理完)|(?:完成了|交好了|提交了|发过去了|发给你了|上传了|填好了|写好了|做完了|处理好了)")
_NOT_DONE = re.compile(r"未完成|没完成|尚未|还没|没有完成|未提交|没提交|未上传|没上传|没有提交|不能完成|无法完成")
_ACK = re.compile(r"^(?:收到|好的|好|明白|了解|可以|没问题|我来|交给我)(?:[，。！!\s]|$)")
_CATEGORIES = (("会议安排", r"会议|开会|例会|参加|出席|集合"),
               ("报名填写", r"报名|填写|填表|问卷|接龙"),
               ("沟通回复", r"回复|确认|答复|沟通"),
               ("材料提交", r"材料|表格|报告|提交|上交|上传|文件|文档"))
_CATEGORY_RES = [(name, re.compile(pattern)) for name, pattern in _CATEGORIES]
_TOKEN = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]{3,}")


def _time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=CN_TZ)
    except (ValueError, TypeError):
        return None


def deadline_of(text: str, sent_at: str) -> str | None:
    """Interpret deadline expressions relative to the source message time."""
    base = _time(sent_at)
    if not base or not re.search(r"前|截止|截至|最晚|最迟|以内", text):
        return None
    date = None
    relative = re.search(r"今天|今晚|明天|明晚|后天", text)
    if relative:
        word = relative.group()
        date = base + timedelta(days=2 if word == "后天" else 1 if word in ("明天", "明晚") else 0)
    else:
        explicit = re.search(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})[日号]", text)
        iso_date = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", text)
        weekday = re.search(r"(下周|本周|这周|周)([一二三四五六日天])", text)
        if not explicit and iso_date:
            explicit = iso_date
        if explicit:
            year, month, day = explicit.groups()
            try:
                date = base.replace(year=int(year or base.year), month=int(month), day=int(day))
                if not year and date.date() < base.date():
                    date = date.replace(year=date.year + 1)
            except ValueError:
                return None
        elif weekday:
            week, day = weekday.groups()
            target = '一二三四五六日'.index('日' if day == '天' else day)
            offset = target - base.weekday()
            if week == '下周':
                offset += 7
            elif week == '周' and offset < 0:
                offset += 7
            date = base + timedelta(days=offset)
    if date is None:
        return None
    time_match = re.search(r"(上午|下午|晚上|中午)?\s*([0-9一二三四五六七八九十两]{1,3})(?:[:：](\d{2})|[点时](?:(\d{1,2}|半)分?)?)", text)
    hour, minute = 23, 59
    if time_match:
        part, hours, minutes, cn_minutes = time_match.groups()
        digits = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
                  '六': 6, '七': 7, '八': 8, '九': 9}
        if hours.isdigit():
            hour = int(hours)
        elif '十' in hours:
            left, _, right = hours.partition('十')
            if (left and left not in digits) or (right and right not in digits):
                return None
            hour = (digits.get(left, 1) * 10) + digits.get(right, 0)
        elif hours in digits:
            hour = digits[hours]
        else:
            return None
        minute = 30 if cn_minutes == '半' else int(minutes or cn_minutes or 0)
        if part in ("下午", "晚上") and hour < 12:
            hour += 12
        if part == "中午" and hour < 11:
            hour += 12
        if hour > 23 or minute > 59:
            return None
    return date.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()


def selected_group(message: dict[str, Any]) -> bool:
    name = (message.get('conversation_name') or '').strip()
    gid = str(message.get('conversation_id') or '')
    if gid in personal.EXCLUDED_GROUP_IDS or name in personal.EXCLUDED_GROUPS:
        return False
    if gid in personal.WORK_GROUP_IDS | personal.WATCH_GROUP_IDS:
        return True
    # Legacy configurations contain names only; unbound names remain usable.
    ids_by_name = personal._CONFIG.get('group_ids', {}) or {}
    bound_names = set()
    for level in ('work', 'watch'):
        values = ids_by_name.get(level) or {}
        if isinstance(values, dict):
            bound_names.update(values.values())
    return name not in bound_names and name in personal.WORK_GROUPS | personal.WATCH_GROUPS


def classify(message: dict[str, Any]) -> dict[str, Any] | None:
    """Return a task candidate only for selected groups and configured identity."""
    group = (message.get("conversation_name") or "").strip()
    if not selected_group(message):
        return None
    if not personal.IDENTITY_CONFIGURED or (message.get("sender_name") or "").strip() in personal.MY_SENDER_NAMES:
        return None
    text = (message.get("text") or "").strip()
    if not text:
        return None
    names = personal.MY_NICKNAMES | ({personal.MY_REALNAME} if personal.MY_REALNAME else set())
    if personal.MY_SHORTNAME:
        names.add(personal.MY_SHORTNAME)
    direct = any(re.search(r"(?<![\u4e00-\u9fff])(?:@[^\s@，,。:：]{0,20}?)?" + re.escape(n) + r"(?=[\s，,。:：！!？?]|你|请|麻烦|帮|负责|$)", text) for n in names)
    action = bool(_ACTION.search(text) or _REPLY_REQUEST.search(text))
    if not direct:
        return None
    # Work terms alone do not identify the owner.
    status = "pending" if direct and action else "review" if direct else None
    if not status:
        return None
    category = next((label for label, pattern in _CATEGORY_RES if pattern.search(text)), "其他")
    return {"status": status, "category": category,
            "deadline": deadline_of(text, message.get("sent_at") or ""),
            "reason": "明确指向本人并包含行动" if status == "pending" else "提及本人，行动不明确"}


def init_store(conn: sqlite3.Connection, baseline: bool = True) -> None:
    """Additive storage; initial cursor intentionally excludes existing history."""
    conn.execute("""CREATE TABLE IF NOT EXISTS task_pipeline_state
        (name TEXT PRIMARY KEY, value TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS personal_tasks (
        id INTEGER PRIMARY KEY, message_id INTEGER NOT NULL UNIQUE,
        conversation_id TEXT NOT NULL, conversation_name TEXT NOT NULL,
        sender_name TEXT NOT NULL, sent_at TEXT NOT NULL, text TEXT NOT NULL,
        category TEXT NOT NULL, deadline TEXT, status TEXT NOT NULL,
        reason TEXT NOT NULL, completion_message_id INTEGER, updated_at TEXT NOT NULL,
        FOREIGN KEY(message_id) REFERENCES messages(id))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_personal_tasks_active ON personal_tasks(status, deadline)")
    conn.execute("""CREATE TABLE IF NOT EXISTS personal_task_deliveries (
        task_id INTEGER NOT NULL, deadline TEXT NOT NULL, channel TEXT NOT NULL,
        delivered_at TEXT, last_error TEXT, attempted_at TEXT, PRIMARY KEY(task_id, deadline, channel),
        FOREIGN KEY(task_id) REFERENCES personal_tasks(id))""")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(personal_task_deliveries)")}
    if 'attempted_at' not in columns:
        conn.execute("ALTER TABLE personal_task_deliveries ADD COLUMN attempted_at TEXT")
    if baseline:
        row = conn.execute("SELECT COALESCE(MAX(id),0) FROM messages").fetchone()
        conn.execute("INSERT OR IGNORE INTO task_pipeline_state VALUES ('cursor',?)", (str(row[0]),))
        conn.execute("INSERT OR IGNORE INTO task_pipeline_state VALUES ('baseline',?)", (datetime.now(CN_TZ).isoformat(),))
    conn.commit()


def initialize_database(db_path: Path) -> None:
    """Back up the live SQLite database before installing the new tables once."""
    path = Path(db_path)
    conn = sqlite3.connect(str(path), timeout=30)
    try:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_pipeline_state'").fetchone()
        if not exists:
            backup = path.with_name(path.name + ".before-personal-tasks-" + datetime.now(CN_TZ).strftime("%Y%m%d-%H%M%S-%f") + ".bak")
            with sqlite3.connect(str(backup)) as target:
                conn.backup(target)
        init_store(conn)
    finally:
        conn.close()


def _same_task(reply: str, original: str) -> bool:
    # Require an explicit shared subject, excluding generic work/action words.
    ignored = {"已经", "完成", "提交", "处理", "这个", "那个", "已经完成", "我已经", "我已", "请", "麻烦"}
    def terms(text: str) -> set[str]:
        chunks = _TOKEN.findall(text)
        return {c[i:i+2] for c in chunks for i in range(len(c)-1)
                if c[i:i+2] not in ignored} | {c.lower() for c in chunks if c.isascii() and len(c) >= 3}
    return bool(terms(reply) & terms(original))


def process_batch(conn: sqlite3.Connection, limit: int = 200) -> dict[str, int]:
    """Apply messages and cursor in one transaction. Caller initializes store."""
    conn.row_factory = sqlite3.Row
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    state = conn.execute("SELECT value FROM task_pipeline_state WHERE name='cursor'").fetchone()
    if not state:
        raise RuntimeError("task pipeline has not been initialized")
    baseline = conn.execute("SELECT value FROM task_pipeline_state WHERE name='baseline'").fetchone()
    base_time = _time(baseline[0]) if baseline else None
    rows = conn.execute("SELECT id,conversation_id,conversation_name,sender_name,sent_at,text FROM messages WHERE id>? ORDER BY id LIMIT ?", (int(state[0]), limit)).fetchall()
    result = {"processed": 0, "created": 0, "completed": 0}
    with conn:
        for row in rows:
            msg = dict(row)
            result["processed"] += 1
            timestamp = _time(msg["sent_at"])
            if base_time and timestamp and timestamp < base_time:
                continue
            group = (msg["conversation_name"] or "").strip()
            if not selected_group(msg):
                continue
            sender = (msg["sender_name"] or "").strip()
            if sender in personal.MY_SENDER_NAMES:
                text = (msg["text"] or "").strip()
                if _DONE.search(text) and not _NOT_DONE.search(text):
                    candidates = conn.execute("SELECT id,text FROM personal_tasks WHERE conversation_id=? AND status IN ('pending','accepted','in_progress')", (msg["conversation_id"],)).fetchall()
                    clauses = [part for part in re.split(r'[，,。；;！？!?\n]', text) if _DONE.search(part)]
                    matches = [task for task in candidates if any(_same_task(part, task["text"]) for part in clauses)]
                    if len(matches) == 1:
                        conn.execute("UPDATE personal_tasks SET status='completed', completion_message_id=?, updated_at=? WHERE id=?", (msg["id"], datetime.now(CN_TZ).isoformat(), matches[0]["id"]))
                        result["completed"] += 1
                elif _ACK.match(text):
                    # A short acknowledgement is attributable only when there is
                    # exactly one active candidate in this conversation and it is recent.
                    candidates = conn.execute("SELECT id,sent_at FROM personal_tasks WHERE conversation_id=? AND status='pending'", (msg["conversation_id"],)).fetchall()
                    if len(candidates) == 1:
                        task_time = _time(candidates[0]["sent_at"])
                        if timestamp and task_time and timedelta(0) <= timestamp - task_time <= timedelta(minutes=10):
                            conn.execute("UPDATE personal_tasks SET status='accepted', updated_at=? WHERE id=?", (datetime.now(CN_TZ).isoformat(), candidates[0]["id"]))
                continue
            candidate = classify(msg)
            if candidate:
                cur = conn.execute("""INSERT OR IGNORE INTO personal_tasks
                    (message_id,conversation_id,conversation_name,sender_name,sent_at,text,category,deadline,status,reason,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (msg["id"], msg["conversation_id"], group, sender, msg["sent_at"], msg["text"], candidate["category"], candidate["deadline"], candidate["status"], candidate["reason"], datetime.now(CN_TZ).isoformat()))
                result["created"] += cur.rowcount
        if rows:
            conn.execute("UPDATE task_pipeline_state SET value=? WHERE name='cursor'", (str(rows[-1]["id"]),))
    return result


def active_tasks(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""SELECT * FROM personal_tasks
        WHERE status IN ('pending','accepted','in_progress')
        ORDER BY CASE WHEN deadline IS NULL THEN 1 ELSE 0 END, deadline, id""")
    return [dict(row) for row in rows]


def send_due_alerts(conn: sqlite3.Connection, send_mail, send_desktop,
                    now: datetime | None = None, retry_seconds: int = 180) -> dict[str, int]:
    """Send mail individually and collapse desktop alerts from the same batch."""
    now = now or datetime.now(CN_TZ)
    result = {"urgent": 0, "mail": 0, "desktop": 0}
    pending_desktop = []

    def pending(task, channel):
        row = conn.execute("SELECT delivered_at, attempted_at FROM personal_task_deliveries WHERE task_id=? AND deadline=? AND channel=?", (task['id'], task['deadline'], channel)).fetchone()
        if row and row[0]:
            return False
        attempted = _time(row[1]) if row and row[1] else None
        return not attempted or (now - attempted).total_seconds() >= retry_seconds

    def record(task, channel, success, error):
        with conn:
            conn.execute("""INSERT INTO personal_task_deliveries
                (task_id,deadline,channel,delivered_at,last_error,attempted_at) VALUES (?,?,?,?,?,?)
                ON CONFLICT(task_id,deadline,channel) DO UPDATE SET
                delivered_at=excluded.delivered_at,last_error=excluded.last_error,attempted_at=excluded.attempted_at""",
                (task['id'], task['deadline'], channel, now.isoformat() if success else None,
                 error, now.isoformat()))

    def deliver(send, event):
        try:
            outcome = send(event)
            success = bool(outcome.get('sent')) if isinstance(outcome, dict) else bool(outcome)
            return success, None if success else '请求未被接受'
        except Exception as exc:
            return False, type(exc).__name__

    for task in active_tasks(conn):
        deadline = _time(task.get('deadline'))
        if deadline is None or not timedelta(0) <= deadline - now <= timedelta(hours=24):
            continue
        result['urgent'] += 1
        event = dict(task, urgent_reason='24小时内截止')
        if pending(task, 'mail'):
            success, error = deliver(send_mail, event)
            record(task, 'mail', success, error)
            result['mail'] += int(success)
        if pending(task, 'desktop'):
            pending_desktop.append(task)

    if pending_desktop:
        if len(pending_desktop) == 1:
            event = dict(pending_desktop[0], urgent_reason='24小时内截止')
        else:
            event = {'conversation_name': '多条任务', 'text': f"有 {len(pending_desktop)} 项任务在24小时内截止，请打开工作台查看。", 'deadline': ''}
        success, error = deliver(send_desktop, event)
        for task in pending_desktop:
            record(task, 'desktop', success, error)
        result['desktop'] = len(pending_desktop) if success else 0
    return result


def send_current_digest(conn: sqlite3.Connection, send_mail, kind: str = "hourly",
                        now: datetime | None = None) -> dict[str, Any]:
    """Summarize current open tasks, including previously alerted tasks."""
    now = now or datetime.now(CN_TZ)
    tasks = active_tasks(conn)
    count = conn.execute("SELECT COUNT(*) FROM personal_tasks WHERE status='review'").fetchone()[0]
    if not tasks and not count:
        return {"sent": False, "skipped": "无待办和待确认事项", "open": 0, "review": 0}
    groups: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        groups.setdefault(task["category"], []).append(task)
    payload = {"kind": kind, "date": now.date().isoformat(),
               "generated_at": now.isoformat(), "groups": groups,
               "resolved": [], "summary": f"另有 {count} 条待确认信息" if count else "",
               "stats": {"open": len(tasks), "review": count, "groups": len(groups), "resolved": 0}}
    result = send_mail(payload)
    return {"sent": bool(result.get("sent")), "open": len(tasks), "review": count,
            "detail": result.get("detail")}
