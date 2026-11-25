"""SQLite 连接与 schema：get_db / init_db / _migrate_db。

这是生产 schema 的唯一定义点（原 db.py 的 Storage 类是死代码，
已于 2026-11-17 P0 删除；两套同名表不同列的陷阱见审计报告 §2-陷阱3）。
"""
from __future__ import annotations

import sqlite3

from messagehub.config import DB_PATH

MESSAGE_TIME_SQL = "COALESCE(CASE WHEN sent_at GLOB '????-??-??*' THEN julianday(sent_at) END, 0)"


def get_db() -> sqlite3.Connection:
    """简单 SQLite 连接。

    全新 clone 后 runtime/ 目录不存在（gitignore），SQLite 无法在
    缺失的目录里建库——首次启动直接 OperationalError。这里兜底自建。
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """创建必要表结构。"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_platform TEXT NOT NULL,
            source_method TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            conversation_name TEXT NOT NULL,
            sender_name TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            text TEXT NOT NULL,
            message_type TEXT DEFAULT 'text',
            fingerprint TEXT UNIQUE,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            assignee TEXT,
            my_role TEXT,
            deadline TEXT,
            category TEXT,
            confidence REAL,
            requires_reply BOOLEAN DEFAULT 0,
            reply_suggestion TEXT,
            source_message_ids TEXT,
            evidence_text TEXT,
            status TEXT DEFAULT 'uncertain',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS import_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_name TEXT NOT NULL,
            imported_at TEXT DEFAULT CURRENT_TIMESTAMP,
            message_count INTEGER DEFAULT 0,
            task_count INTEGER DEFAULT 0
        )
    """)

    # FTS5 全文索引
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
            text, content='messages', content_rowid='id'
        )
    """)

    # 采集状态（区分：成功无新/失败/不完整/未开始）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS capture_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_label TEXT NOT NULL,
            source_path TEXT,
            started_at TEXT DEFAULT CURRENT_TIMESTAMP,
            finished_at TEXT,
            status TEXT DEFAULT 'running',
            new_count INTEGER DEFAULT 0,
            dup_count INTEGER DEFAULT 0,
            error TEXT,
            note TEXT
        )
    """)

    # 已扫描文件状态（增量去重：记录文件 hash + mtime + 行数）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scan_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_path TEXT UNIQUE NOT NULL,
            content_hash TEXT,
            mtime REAL,
            size INTEGER,
            parsed_rows INTEGER DEFAULT 0,
            last_scanned TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 安全迁移：补充旧表可能缺失的列
    _migrate_db(conn)

    conn.commit()
    conn.close()


def _migrate_db(conn: sqlite3.Connection) -> None:
    """为已存在的表补充缺失列（开发期向前兼容）。"""
    cur = conn.cursor()
    # messages 表
    cur.execute("PRAGMA table_info(messages)")
    cols = {r[1] for r in cur.fetchall()}
    for col, ctype in (
        ("sender_ref", "TEXT"),
        ("account_ref", "TEXT"),
        ("source_message_id", "TEXT"),
        ("time_confidence", "TEXT"),
        ("capture_status", "TEXT"),
    ):
        if col not in cols:
            cur.execute(f"ALTER TABLE messages ADD COLUMN {col} {ctype}")
    # Building the index can spill into a Windows temp directory SQLite cannot open.
    # Keep the sort in memory on this short-lived migration connection.
    cur.execute("PRAGMA temp_store=MEMORY")
    cur.execute(f"""CREATE INDEX IF NOT EXISTS idx_messages_conversation_time
        ON messages(source_platform, conversation_id, {MESSAGE_TIME_SQL} DESC, id DESC)""")
    # tasks 表
    cur.execute("PRAGMA table_info(tasks)")
    tcols = {r[1] for r in cur.fetchall()}
    for col, ctype in (
        ("assignee", "TEXT"),
        ("my_role", "TEXT"),
        ("deadline", "TEXT"),
        ("requires_reply", "BOOLEAN DEFAULT 0"),
        ("reply_suggestion", "TEXT"),
        ("source_message_ids", "TEXT"),
        ("evidence_text", "TEXT"),
        ("confidence", "REAL"),
        ("fingerprint", "TEXT"),
    ):
        if col not in tcols:
            cur.execute(f"ALTER TABLE tasks ADD COLUMN {col} {ctype}")
