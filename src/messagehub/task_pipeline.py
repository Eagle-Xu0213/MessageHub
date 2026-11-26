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

from messagehub import personal, task_locations
from messagehub.config import CN_TZ

_ACTION = re.compile(r"提交|上交|填写|报名|回复|确认|发送|发给|发我|交给|整理|准备|完成|处理|跟进|核对|签字|盖章|参加|出席|写|制作|做|领取|上传|请.{0,8}交")
_REPLY_REQUEST = re.compile(r"(?:能|是否|可不可以|能不能|有没有|行不行|可以).{0,20}(?:吗|么|\?|？)|请.{0,8}(?:回复|确认)")
_WORK = re.compile(r"提交|材料|表格|报名|回复|确认|会议|开会|任务|截止|通知|工作|报告|安排|文件|负责|处理|跟进|填写|上传|完成|工时|考勤|报销|工资|项目|作业|课程|审核|值班|证明|盖章|问卷")
_DONE = re.compile(r"(?:已经|已|我)(?:经)?(?:完成|提交|交了|发了|发送|上传|填好|写好|做好|处理完)|(?:完成了|交好了|提交了|发过去了|发给你了|上传了|填好了|写好了|做完了|处理好了)")
_NOT_DONE = re.compile(r"未完成|没完成|尚未|还没|没有完成|未提交|没提交|未上传|没上传|没有提交|不能完成|无法完成")
_ACK = re.compile(r"^(?:收到|好的|好|明白|了解|可以|没问题|我来|交给我)(?:[，。！!\s]|$)")
_CATEGORIES = (("会议活动", r"会议|开会|例会|参加|出席|集合"),
               ("报名填写", r"报名|填写|填表|问卷|接龙"),
               ("沟通确认", r"回复|确认|答复|沟通"),
               ("材料提交", r"材料|表格|报告|提交|上交|上传|文件|文档"))
_CATEGORY_RES = [(name, re.compile(pattern)) for name, pattern in _CATEGORIES]
_TOKEN = re.compile(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9]{3,}")
_BARE_MENTION = re.compile(r'^\s*@[^\s@，,。:：！!？?]+[\s，,。:：！!？?]*$')


def _context_candidate(mention: dict[str, Any], nearby: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Use an adjacent message from the same sender, without crossing conversation turns."""
    if not _BARE_MENTION.fullmatch((mention.get('text') or '').strip()):
        return None
    at = _time(mention.get('sent_at'))
    if not at:
        return None
    for message in nearby:
        if message.get("source_platform") != mention.get("source_platform"):
            break
        when = _time(message.get('sent_at'))
        if not when or abs((when - at).total_seconds()) > 300:
            continue
        text = (message.get('text') or '').strip()
        if not text:
            continue
        # Another speaker or another directed mention marks a different turn.
        if message.get('sender_name') != mention.get('sender_name') or '@' in text:
            break
        if _WORK.search(text):
            category = next((label for label, pattern in _CATEGORY_RES if pattern.search(text)), '其他')
            action = bool(_ACTION.search(text) or _REPLY_REQUEST.search(text))
            return ({'status': 'pending' if action else 'review', 'category': category,
                     'deadline': deadline_of(text, message.get('sent_at') or ''),
                     'reason': '根据相邻消息整理，请核对原文'}, message)
        break
    return None


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


def selected_work_contact(message: dict[str, Any]) -> bool:
    """Selected work or mixed contacts, excluding groups and excluded contacts."""
    name = (message.get('conversation_name') or '').strip()
    gid = str(message.get('conversation_id') or '')
    if gid.endswith('@chatroom') or gid in (personal.WORK_GROUP_IDS | personal.WATCH_GROUP_IDS | personal.EXCLUDED_GROUP_IDS):
        return False
    if name in (personal.WORK_GROUPS | personal.WATCH_GROUPS | personal.EXCLUDED_GROUPS):
        return False
    return name in (personal.WORK_CONTACTS | personal.WATCH_CONTACTS) and name not in personal.EXCLUDED_CONTACTS


def classify(message: dict[str, Any]) -> dict[str, Any] | None:
    """Return a task candidate only for selected groups and configured identity."""
    group = (message.get("conversation_name") or "").strip()
    work_contact = selected_work_contact(message)
    if not selected_group(message) and not work_contact:
        return None
    if not personal.IDENTITY_CONFIGURED or (message.get("sender_name") or "").strip() in personal.MY_SENDER_NAMES:
        return None
    text = (message.get("text") or "").strip()
    if not text:
        return None
    if work_contact:
        action = bool(_ACTION.search(text) or _REPLY_REQUEST.search(text))
        if not _WORK.search(text):
            return None
        category = next((label for label, pattern in _CATEGORY_RES if pattern.search(text)), '其他')
        return {'status': 'pending' if action else 'review', 'category': category,
                'deadline': deadline_of(text, message.get('sent_at') or ''),
                'reason': '来自工作联系人的待办消息' if action else '工作联系人消息，行动不明确'}
    names = (personal.MY_NICKNAMES or personal.MY_SENDER_NAMES) | ({personal.MY_REALNAME} if personal.MY_REALNAME else set())
    if personal.MY_SHORTNAME:
        names.add(personal.MY_SHORTNAME)
    direct = any(re.search(r"(?<![\u4e00-\u9fff])(?:@[^\s@，,。:：]{0,20}?)?" + re.escape(n) + r"(?=[\s，,。:：！!？?]|你|请|麻烦|帮|负责|$)", text) for n in names)
    action = bool(_ACTION.search(text) or _REPLY_REQUEST.search(text))
    # A mention alone is not work evidence. Context can promote it below.
    if direct and not _WORK.search(text):
        return None
    if not direct:
        # Public notices require both a subject and an explicit announcement signal.
        if not (re.search(r"通知|公告|各位|大家|全体|所有人", text) and re.search(r"会议|开会|报名|截止|提交材料|材料提交|填表|问卷", text)):
            return None
        category = next((label for label, pattern in _CATEGORY_RES if pattern.search(text)), "其他")
        return {"status": "notice", "category": category, "deadline": deadline_of(text, message.get("sent_at") or ""), "reason": "群内公共通知，请核对原文"}
    # Work terms alone do not identify the owner.
    status = "pending" if direct and action else "review" if direct else None
    if not status:
        return None
    category = next((label for label, pattern in _CATEGORY_RES if pattern.search(text)), "其他")
    return {"status": status, "category": category,
            "deadline": deadline_of(text, message.get("sent_at") or ""),
            "reason": "明确指向你且包含具体行动" if status == "pending" else "提及本人，行动不明确"}


def init_store(conn: sqlite3.Connection, baseline: bool = True) -> None:
    """Additive storage; initial cursor intentionally excludes existing history."""
    conn.execute("""CREATE TABLE IF NOT EXISTS task_pipeline_state
        (name TEXT PRIMARY KEY, value TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS personal_tasks (
        id INTEGER PRIMARY KEY, message_id INTEGER UNIQUE,
        conversation_id TEXT NOT NULL, conversation_name TEXT NOT NULL,
        sender_name TEXT NOT NULL, sent_at TEXT NOT NULL, text TEXT NOT NULL,
        category TEXT NOT NULL, deadline TEXT, status TEXT NOT NULL,
        reason TEXT NOT NULL, completion_message_id INTEGER, updated_at TEXT NOT NULL, title TEXT,
        FOREIGN KEY(message_id) REFERENCES messages(id))""")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(personal_tasks)")}
    if "title" not in columns:
        conn.execute("ALTER TABLE personal_tasks ADD COLUMN title TEXT")
    if "source_message_ids" not in columns:
        conn.execute("ALTER TABLE personal_tasks ADD COLUMN source_message_ids TEXT")
    conn.execute("""CREATE TABLE IF NOT EXISTS personal_task_categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, rule_name TEXT UNIQUE)""")
    if conn.execute("""INSERT OR IGNORE INTO task_pipeline_state VALUES ('categories_initialized', '1')""").rowcount:
        conn.executemany("INSERT INTO personal_task_categories(name, rule_name) VALUES (?, ?)",
                         [(name, name) for name in (*[label for label, _ in _CATEGORIES], '其他')])
        conn.execute("""INSERT OR IGNORE INTO personal_task_categories(name)
            SELECT DISTINCT category FROM personal_tasks WHERE category != ''""")
    conn.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_personal_tasks_source_messages
        ON personal_tasks(source_message_ids) WHERE source_message_ids IS NOT NULL""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_personal_tasks_active ON personal_tasks(status, deadline)")
    conn.execute("""CREATE TABLE IF NOT EXISTS personal_task_deliveries (
        task_id INTEGER NOT NULL, deadline TEXT NOT NULL, channel TEXT NOT NULL,
        delivered_at TEXT, last_error TEXT, attempted_at TEXT, PRIMARY KEY(task_id, deadline, channel),
        FOREIGN KEY(task_id) REFERENCES personal_tasks(id))""")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(personal_task_deliveries)")}
    if 'attempted_at' not in columns:
        conn.execute("ALTER TABLE personal_task_deliveries ADD COLUMN attempted_at TEXT")
    task_locations.init_store(conn)
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
        task_info = conn.execute("PRAGMA table_info(personal_tasks)").fetchall() if exists else []
        needs_migration = bool(task_info and (not any(r[1] == "title" for r in task_info) or any(r[1] == "message_id" and r[3] for r in task_info)))
        if not exists or needs_migration:
            backup = path.with_name(path.name + ".before-personal-tasks-" + datetime.now(CN_TZ).strftime("%Y%m%d-%H%M%S-%f") + ".bak")
            with sqlite3.connect(str(backup)) as target:
                conn.backup(target)
        if needs_migration:
            # Copy into a replacement table without renaming the original, preserving
            # delivery-table foreign keys and all existing task IDs.
            conn.execute("""CREATE TABLE personal_tasks_new (
                id INTEGER PRIMARY KEY, message_id INTEGER UNIQUE,
                conversation_id TEXT NOT NULL, conversation_name TEXT NOT NULL,
                sender_name TEXT NOT NULL, sent_at TEXT NOT NULL, text TEXT NOT NULL,
                category TEXT NOT NULL, deadline TEXT, status TEXT NOT NULL,
                reason TEXT NOT NULL, completion_message_id INTEGER, updated_at TEXT NOT NULL,
                title TEXT, source_message_ids TEXT, FOREIGN KEY(message_id) REFERENCES messages(id))""")
            names = [r[1] for r in task_info if r[1] != 'title']
            columns = ','.join(names)
            title_expr = 'title' if any(r[1] == 'title' for r in task_info) else 'NULL'
            conn.execute(f"INSERT INTO personal_tasks_new ({columns},title) SELECT {columns},{title_expr} FROM personal_tasks")
            conn.execute("DROP TABLE personal_tasks")
            conn.execute("ALTER TABLE personal_tasks_new RENAME TO personal_tasks")
            conn.execute("UPDATE personal_tasks SET category='会议活动' WHERE category='会议安排'")
            conn.execute("UPDATE personal_tasks SET category='沟通确认' WHERE category='沟通回复'")
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
    rows = conn.execute("SELECT * FROM messages WHERE id>? ORDER BY id LIMIT ?", (int(state[0]), limit)).fetchall()
    categories = dict(conn.execute("SELECT rule_name, name FROM personal_task_categories WHERE rule_name IS NOT NULL"))
    result = {"processed": 0, "created": 0, "completed": 0}
    with conn:
        for row in rows:
            msg = dict(row)
            result["processed"] += 1
            if msg.get("source_platform") == "qq":
                continue
            timestamp = _time(msg["sent_at"])
            if base_time and timestamp and timestamp < base_time:
                continue
            group = (msg["conversation_name"] or "").strip()
            if not selected_group(msg) and not selected_work_contact(msg):
                continue
            sender = (msg["sender_name"] or "").strip()
            if sender in personal.MY_SENDER_NAMES:
                text = (msg["text"] or "").strip()
                if _DONE.search(text) and not _NOT_DONE.search(text):
                    candidates = conn.execute("""SELECT id,text FROM personal_tasks
                        WHERE conversation_id=? AND source_message_ids IS NULL
                        AND status IN ('pending','accepted','in_progress')""", (msg["conversation_id"],)).fetchall()
                    clauses = [part for part in re.split(r'[，,。；;！？!?\n]', text) if _DONE.search(part)]
                    matches = [task for task in candidates if any(_same_task(part, task["text"]) for part in clauses)]
                    if len(matches) == 1:
                        conn.execute("UPDATE personal_tasks SET status='completed', completion_message_id=?, updated_at=? WHERE id=?", (msg["id"], datetime.now(CN_TZ).isoformat(), matches[0]["id"]))
                        result["completed"] += 1
                elif _ACK.match(text):
                    # A short acknowledgement is attributable only when there is
                    # exactly one active candidate in this conversation and it is recent.
                    candidates = conn.execute("""SELECT id,sent_at FROM personal_tasks
                        WHERE conversation_id=? AND source_message_ids IS NULL AND status='pending'""",
                        (msg["conversation_id"],)).fetchall()
                    if len(candidates) == 1:
                        task_time = _time(candidates[0]["sent_at"])
                        if timestamp and task_time and timedelta(0) <= timestamp - task_time <= timedelta(minutes=10):
                            conn.execute("UPDATE personal_tasks SET status='accepted', updated_at=? WHERE id=?", (datetime.now(CN_TZ).isoformat(), candidates[0]["id"]))
                continue
            # A bare mention may follow its instruction in a separate WeChat message.
            candidate = classify(msg)
            contextual_title = None
            contextual_text = None
            bare_mention = bool(_BARE_MENTION.fullmatch((msg['text'] or '').strip()))
            names = (personal.MY_NICKNAMES or personal.MY_SENDER_NAMES) | ({personal.MY_REALNAME} if personal.MY_REALNAME else set()) | ({personal.MY_SHORTNAME} if personal.MY_SHORTNAME else set())
            mine = bare_mention and any(name and re.fullmatch(r'@[^\s@，,。:：！!？?]*?' + re.escape(name) + r'[\s，,。:：！!？?]*', msg['text'].strip()) for name in names)
            if mine:
                previous = conn.execute(
                    'SELECT * FROM messages '
                    'WHERE conversation_id=? AND id<? ORDER BY id DESC LIMIT 1',
                    (msg['conversation_id'], msg['id'])).fetchall()
                nearby = [dict(row) for row in previous if (not base_time or (_time(row['sent_at']) and _time(row['sent_at']) >= base_time))]
                matched = _context_candidate(msg, nearby)
                if matched:
                    candidate, source = matched
                    contextual_text = source['text']
                    contextual_title = contextual_text.strip()[:200]
            # A work-related follow-up may arrive after an otherwise bare @.
            previous_message = conn.execute('''SELECT *
                FROM messages WHERE conversation_id=? AND id<? ORDER BY id DESC LIMIT 1''',
                (msg['conversation_id'], msg['id'])).fetchone()
            if previous_message and personal.IDENTITY_CONFIGURED:
                mention = dict(previous_message)
                mention_time = _time(mention['sent_at'])
                is_mine = any(name and re.fullmatch(r'@[^\s@，,。:：！!？?]*?' + re.escape(name) + r'[\s，,。:：！!？?]*', mention['text'].strip()) for name in names)
                if is_mine and (not base_time or (mention_time and mention_time >= base_time)):
                    matched = _context_candidate(mention, [msg])
                    if matched:
                        info, source = matched
                        cur = conn.execute('''INSERT OR IGNORE INTO personal_tasks
                            (message_id,conversation_id,conversation_name,sender_name,sent_at,text,category,deadline,status,reason,updated_at,title)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                            (mention['id'], mention['conversation_id'], mention['conversation_name'], mention['sender_name'],
                             mention['sent_at'], mention['text'], categories.get(info['category'], ''), info['deadline'], info['status'],
                             info['reason'], datetime.now(CN_TZ).isoformat(), source['text'].strip()[:200]))
                        if cur.rowcount:
                            task_locations.apply_recognized(conn, cur.lastrowid, datetime.now(CN_TZ).isoformat(),
                                                           mention['text'], source['text'])
                        result['created'] += cur.rowcount
            # Also handle the instruction arriving in a later capture batch.
            previous_task = conn.execute('''SELECT m.*,t.id AS task_id,t.locations_manually_set
                FROM personal_tasks t JOIN messages m ON m.id=t.message_id
                WHERE t.conversation_id=? AND t.message_id<?
                AND t.status='review' AND t.title IS NULL AND t.reason='提及本人，行动不明确'
                AND NOT EXISTS (SELECT 1 FROM messages n WHERE n.conversation_id=m.conversation_id
                                AND n.id>m.id AND n.id<?)
                ORDER BY t.message_id DESC LIMIT 1''',
                (msg['conversation_id'], msg['id'], msg['id'])).fetchone()
            if previous_task:
                matched = _context_candidate(dict(previous_task), [msg])
                if matched:
                    info, source = matched
                    updated = conn.execute('''UPDATE personal_tasks SET status=?,category=?,deadline=?,reason=?,title=?,updated_at=?
                        WHERE id=? AND status='review' AND title IS NULL''',
                        (info['status'], categories.get(info['category'], ''), info['deadline'], info['reason'],
                         source['text'].strip()[:200], datetime.now(CN_TZ).isoformat(), previous_task['task_id']))
                    if updated.rowcount and not previous_task['locations_manually_set']:
                        task_locations.apply_recognized(conn, previous_task['task_id'], datetime.now(CN_TZ).isoformat(),
                                                       previous_task['text'], source['text'])
            if candidate:
                cur = conn.execute("""INSERT OR IGNORE INTO personal_tasks
                    (message_id,conversation_id,conversation_name,sender_name,sent_at,text,category,deadline,status,reason,updated_at,title)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (msg["id"], msg["conversation_id"], group, sender, msg["sent_at"], msg["text"], categories.get(candidate["category"], ''), candidate["deadline"], candidate["status"], candidate["reason"], datetime.now(CN_TZ).isoformat(), contextual_title))
                if cur.rowcount:
                    task_locations.apply_recognized(conn, cur.lastrowid, datetime.now(CN_TZ).isoformat(),
                                                   msg["text"], contextual_text)
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
            return success, None if success else '通知发送请求未被接受'
        except Exception as exc:
            return False, type(exc).__name__

    for task in active_tasks(conn):
        deadline = _time(task.get('deadline'))
        if deadline is None or not timedelta(0) <= deadline - now <= timedelta(hours=24):
            continue
        result['urgent'] += 1
        event = dict(task, urgent_reason='24 小时内截止')
        if send_mail is not None and pending(task, 'mail'):
            success, error = deliver(send_mail, event)
            record(task, 'mail', success, error)
            result['mail'] += int(success)
        if send_desktop is not None and pending(task, 'desktop'):
            pending_desktop.append(task)

    if pending_desktop:
        if len(pending_desktop) == 1:
            event = dict(pending_desktop[0], urgent_reason='24 小时内截止')
        else:
            event = {'conversation_name': '多条待办事项', 'text': f"有 {len(pending_desktop)} 项待办在 24 小时内截止，请打开工作台查看。", 'deadline': ''}
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
