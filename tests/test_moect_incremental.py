"""Moect 采集器增量/分页/水位逻辑的模拟测试（不接触真实微信）。

覆盖：
- 超过 3000 条新增消息（limit 截断）
- 同秒多条消息（排他游标）
- 达到 limit 后仍有未处理消息
- 入库中断（部分批次失败）
- 重启后继续补读
- 相同消息重复处理（幂等）

构造与 Moect 快照同构的临时 SQLite 库：contact/contact.db、
message/message_0.db（Name2Id + Msg_<md5> 表 + zstd 压缩正文）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest
import zstandard

ROOT = Path(__file__).resolve().parents[1]
MOD_PATH = ROOT / "scripts" / "moect_live.py"


def _load_module():
    """按文件路径加载 moect_live.py（scripts 不是包）。"""
    spec = importlib.util.spec_from_file_location("moect_live_under_test", MOD_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ml():
    return _load_module()


CHAT = "11111111111@chatroom"
CHAT2 = "22222222222@chatroom"
PRIV = "wxid_private0001"


def _table_name(conv_id: str) -> str:
    return "Msg_" + hashlib.md5(conv_id.encode()).hexdigest()


def build_snapshot(root: Path, messages: list[tuple]) -> Path:
    """生成一个与 Moect 快照同构的最小目录。

    messages: [(conv_id, local_id, server_id, local_type, create_time, text)]
    文本正文按微信 4.x 习惯用 zstd 压缩，并设 WCDB_CT_message_content=4。
    """
    snap = root / "snapshot_test"
    (snap / "contact").mkdir(parents=True, exist_ok=True)
    (snap / "message").mkdir(parents=True, exist_ok=True)

    c = sqlite3.connect(snap / "contact" / "contact.db")
    c.execute("CREATE TABLE contact (username TEXT, nick_name TEXT, remark TEXT)")
    for conv, name in ((CHAT, "测试群甲"), (CHAT2, "测试群乙"), (PRIV, "某私聊")):
        c.execute("INSERT INTO contact VALUES (?,?,?)", (conv, name, ""))
    c.commit()
    c.close()

    db = sqlite3.connect(snap / "message" / "message_0.db")
    db.execute("CREATE TABLE Name2Id (user_name TEXT)")
    convs = sorted({m[0] for m in messages})
    rid_of = {}
    for conv in convs:
        cur = db.execute("INSERT INTO Name2Id (user_name) VALUES (?)", (conv,))
        rid_of[conv] = cur.lastrowid
    senders = ["wxid_senderA", "wxid_senderB", "wxid_senderC"]
    for conv in convs:
        rid_of[senders[0]] = db.execute(
            "INSERT INTO Name2Id (user_name) VALUES (?)", (senders[0],)).lastrowid
    zc = zstandard.ZstdCompressor()
    for conv in convs:
        t = _table_name(conv)
        db.execute(
            f'CREATE TABLE "{t}" (local_id INTEGER, server_id INTEGER, '
            f"local_type INTEGER, real_sender_id INTEGER, create_time INTEGER, "
            f"message_content BLOB, WCDB_CT_message_content INTEGER)"
        )
    for conv, lid, sid, ltype, ct, text in messages:
        t = _table_name(conv)
        payload = zc.compress(text.encode("utf-8")) if ltype == 1 else b"\x00\x01"
        db.execute(
            f'INSERT INTO "{t}" VALUES (?,?,?,?,?,?,?)',
            (lid, sid, ltype, rid_of[senders[0]], ct, payload,
             4 if ltype == 1 else 0),
        )
    db.commit()
    db.close()
    return snap


class _Args:
    def __init__(self, **kw):
        self.no_refresh = True
        self.include_private = False
        self.limit = kw.get("limit", 3000)
        self.since_hours = 24.0


# ---------------------------------------------------------------- 基础增量
def test_reads_plain_text_only(ml, tmp_path):
    """只采群聊纯文本：图片/表情/私聊一律不采。"""
    snap = build_snapshot(tmp_path, [
        (CHAT, 1, 100, 1, 1000, "文本一"),
        (CHAT, 2, 101, 21474836529, 1001, "图片"),      # 图片，应过滤
        (CHAT, 3, 102, 1, 1002, "文本二"),
        (PRIV, 4, 103, 1, 1003, "私聊文本"),              # 私聊，应过滤
    ])
    msgs, _ = ml.read_new_messages(snap, 500, 3000)
    texts = [m["text"] for m in msgs]
    assert texts == ["文本一", "文本二"], texts


def test_dedup_same_snapshot_twice(ml, tmp_path, monkeypatch):
    """同一快照读两次，源消息 ID 必须完全一致（幂等前提）。"""
    snap = build_snapshot(tmp_path, [(CHAT, i, 1000 + i, 1, 1000 + i, f"m{i}")
                                     for i in range(1, 6)])
    a, _ = ml.read_new_messages(snap, 900, 3000)
    b, _ = ml.read_new_messages(snap, 900, 3000)
    assert [m["source_message_id"] for m in a] == [m["source_message_id"] for m in b]


# ---------------------------------------------------------------- 大规模
def test_large_backlog_over_3000(ml, tmp_path):
    """超过 3000 条积压：分页游标必须最终全部取到，且一条不重。"""
    n = 3500
    snap = build_snapshot(tmp_path, [
        (CHAT, i, 10_000 + i, 1, 1_700_000_000 + i, f"bulk{i}")
        for i in range(1, n + 1)
    ])
    seen, cursor = [], None
    pages = 0
    while True:
        page, cursor = ml.read_new_messages_paged(
            snap, cursor=cursor, limit=1000)
        pages += 1
        if not page:
            break
        seen.extend(page)
        if pages > 20:
            pytest.fail("分页未收敛，可能死循环")
    ids = [m["source_message_id"] for m in seen]
    assert len(ids) == n, f"应取到 {n} 条，实际 {len(ids)}"
    assert len(set(ids)) == n, "分页出现重复"
    assert pages >= 4, f"3500 条 / 每页 1000 应至少 4 页，实际 {pages}"


def test_same_second_boundary_not_lost(ml, tmp_path):
    """同一秒超过 limit 条：跨页时不能因游标丢失同秒消息。"""
    ct = 1_700_000_500
    n = 40
    snap = build_snapshot(tmp_path, [
        (CHAT, i, 20_000 + i, 1, ct, f"same{i}") for i in range(1, n + 1)
    ])
    seen, cursor = [], None
    for _ in range(10):
        page, cursor = ml.read_new_messages_paged(snap, cursor=cursor, limit=10)
        if not page:
            break
        seen.extend(page)
    assert len(seen) == n, f"同秒 {n} 条应全部取到，实际 {len(seen)}"
    assert len({m["source_message_id"] for m in seen}) == n


# ---------------------------------------------------------------- 水位/中断
def test_watermark_never_skips_unprocessed(ml, tmp_path):
    """达到 limit 时游标不得越过未处理消息（核心防漏读）。"""
    n = 1200
    snap = build_snapshot(tmp_path, [
        (CHAT, i, 30_000 + i, 1, 1_700_001_000 + i, f"w{i}")
        for i in range(1, n + 1)
    ])
    cur = {"state": "init", "watermark": 1_700_000_000}
    page, cursor = ml.read_new_messages_paged(snap, cur, limit=1000)
    assert len(page) == 1000
    # 游标必须停在已取的最后一条，其后仍有未处理消息
    assert cursor["state"] == "after"
    assert cursor["lid"] == 1000 or cursor["ct"] <= 1_700_002_000
    rest = ml.peek_new_messages(snap, cursor, limit=5)
    assert len(rest) == 5, "游标之后必须还能取到消息，说明没有跳过"
    # 关键：游标之前绝不包含未取到的消息（无洞）
    ids_page = {m["source_message_id"] for m in page}
    ids_rest = {m["source_message_id"] for m in rest}
    assert not (ids_page & ids_rest)
    # 继续翻页应恰好补满剩余 200 条
    page2, _ = ml.read_new_messages_paged(snap, cursor, limit=1000)
    assert len(page2) == 200, f"应剩 200 条，实际 {len(page2)}"


def test_ingest_failure_does_not_advance_checkpoint(ml, tmp_path):
    """入库只确认前 20 条：游标必须停在 20，后 30 条留待重试。"""
    snap = build_snapshot(tmp_path, [
        (CHAT, i, 40_000 + i, 1, 1_700_002_000 + i, f"f{i}")
        for i in range(1, 51)
    ])
    cur = {"state": "init", "watermark": 1_700_001_900}
    page, _next = ml.read_new_messages_paged(snap, cur, limit=50)
    assert len(page) == 50
    c2 = ml.cursor_after_ack(page, acked=20)
    assert c2["state"] == "after"
    assert c2["lid"] == 20, f"游标应停在 local_id 20，实际 {c2['lid']}"
    # 从该游标继续，应恰好取到剩余 30 条，不重不漏
    rest, _ = ml.read_new_messages_paged(
        snap, dict(c2, watermark=1_700_001_900), limit=100)
    assert len(rest) == 30, f"应剩 30 条，实际 {len(rest)}"
    assert rest[0]["text"] == "f21"


def test_restart_resumes_from_checkpoint(ml, tmp_path):
    """重启后从检查点续采，不漏不重。"""
    n = 2500
    snap = build_snapshot(tmp_path, [
        (CHAT, i, 50_000 + i, 1, 1_700_003_000 + i, f"r{i}")
        for i in range(1, n + 1)
    ])
    first, cursor = ml.read_new_messages_paged(snap, cursor=None, limit=1000)
    assert len(first) == 1000
    # 模拟进程重启：仅凭持久化的 cursor 继续
    cursor = json.loads(json.dumps(cursor))
    rest = []
    while True:
        page, cursor = ml.read_new_messages_paged(snap, cursor=cursor, limit=1000)
        if not page:
            break
        rest.extend(page)
    allids = [m["source_message_id"] for m in first + rest]
    assert len(allids) == n, f"应为 {n} 条，实际 {len(allids)}"
    assert len(set(allids)) == n, "续采出现重复"


# ---------------------------------------------------------------- 统计口径
def test_stats_separate_filters(ml, tmp_path):
    """统计必须区分 非文本过滤 / 超长 / 空文本 / 候选。"""
    long_text = "x" * 9000
    snap = build_snapshot(tmp_path, [
        (CHAT, 1, 60_001, 1, 1_700_004_001, "正常一"),
        (CHAT, 2, 60_002, 21474836529, 1_700_004_002, "图片"),
        (CHAT, 3, 60_003, 244813135921, 1_700_004_003, "引用"),
        (CHAT, 4, 60_004, 1, 1_700_004_004, long_text),
        (CHAT, 5, 60_005, 1, 1_700_004_005, "正常二"),
    ])
    msgs, _, stats = ml.read_new_messages_paged(
        snap, cursor=None, limit=1000, want_stats=True)
    assert len(msgs) == 2
    assert stats["scanned"] == 5
    assert stats["filtered_non_text"] == 2
    assert stats["filtered_too_long"] == 1
    assert stats["candidates"] == 2
