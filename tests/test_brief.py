"""R2 简报生成测试：三栏聚合、落库、降级摘要（临时库隔离）。

数据全部虚构，身份/群名与 tests/fixtures/personal_test.json 一致。
"""
import json
import sqlite3

import pytest

from messagehub import brief as brief_mod
from messagehub.brief import build_sections, collect_events, generate_brief, save_brief


@pytest.fixture(autouse=True)
def _no_baseline(monkeypatch, tmp_path):
    """测试隔离：基线文件指到临时目录且不存在（不受生产基线影响）。"""
    monkeypatch.setattr(brief_mod, "BASELINE_FILE", tmp_path / "baseline.json")


def _setup_db(tmp_path, rows):
    """建临时库并写入 messages。rows: (group, sender, sent_at, text)"""
    db = tmp_path / "brief_test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""CREATE TABLE messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, source_platform TEXT, source_method TEXT,
        conversation_id TEXT, conversation_name TEXT, sender_name TEXT, sent_at TEXT,
        text TEXT, message_type TEXT DEFAULT 'text', fingerprint TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    conn.executemany(
        "INSERT INTO messages (source_platform, source_method, conversation_id, "
        "conversation_name, sender_name, sent_at, text) VALUES (?,?,?,?,?,?,?)",
        [("wechat", "live_ingest", "x@chatroom", g, s, t, x) for g, s, t, x in rows])
    conn.commit()
    conn.close()
    return str(db)


ROWS = [
    # 今天：待办（cue+任务，未答复）
    ("混杂群甲", "李四", "2026-11-17T09:00+08:00",
     "张三你就帮我把聘书拿到值班室嘛"),
    # 今天：需回复（@我提问，未答复）
    ("工作群甲", "王五", "2026-11-17T09:10+08:00",
     "@自动化2401张三 今晚能过来吗"),
    # 今天：已应答闭环
    ("工作群甲", "王五", "2026-11-17T09:11+08:00",
     "不行，有课，明天晚上可以"),
    # 今天：通知
    ("工作群乙", "赵六", "2026-11-17T10:00+08:00",
     "11月18日17:00 在主楼E305开展答辩"),
    # 昨天：不应进今天 1 天窗口
    ("工作群丙", "孙七", "2026-11-16T09:00+08:00",
     "本周签到码:6912RE"),
]


@pytest.fixture()
def db(tmp_path):
    return _setup_db(tmp_path, ROWS)


class TestCollectEvents:
    def test_extracts_with_resolution(self, db):
        events = collect_events(days=1, db_path=db)
        assert len(events) == 3
        by_type = {e["event_type"] for e in events}
        assert by_type == {"assignment", "reply_needed", "notice"}
        for e in events:
            assert "reply_status" in e

    def test_day_window(self, db):
        events = collect_events(days=1, db_path=db)
        # 昨天的签到码不进 1 天窗口（按消息日期过滤）
        assert all(e["sent_at"].startswith("2026-11-17") for e in events)


class TestBuildSections:
    def test_three_sections_and_resolved(self, db):
        events = collect_events(days=1, db_path=db)
        s = build_sections(events)
        assert len(s["todos"]) == 1
        assert s["todos"][0]["sender"] == "李四"
        assert len(s["replies"]) == 1
        assert len(s["notices"]) == 1
        assert s["notices"][0]["group"] == "工作群乙"

    def test_resolved_events_excluded_from_open(self, db):
        events = collect_events(days=1, db_path=db)
        s = build_sections(events)
        # 已应答的"今晚能过来吗"不该出现在 replies
        assert all(e["message_id"] != events[1]["message_id"]
                   for e in s["replies"]) or events[1]["reply_status"] == "unanswered"


class TestSaveAndGenerate:
    def test_generate_and_persist(self, db):
        brief = generate_brief(days=1, save=True, db_path=db)
        assert brief["stats"]["todos"] == 1
        assert brief["stats"]["replies"] == 1
        assert brief["stats"]["notices"] == 1
        assert brief["ai_summary"]
        assert isinstance(brief["degraded"], bool)

        # 落库验证
        conn = sqlite3.connect(db)
        row = conn.execute("SELECT date, summary FROM briefs").fetchone()
        conn.close()
        assert row[0] == brief["date"]
        saved = json.loads(row[1])
        assert saved["stats"]["todos"] == 1

    def test_rerun_overwrites_same_row(self, db):
        b1 = generate_brief(days=1, save=True, db_path=db)
        b2 = generate_brief(days=1, save=True, db_path=db)
        assert b2["brief_id"] == b1["brief_id"]
        conn = sqlite3.connect(db)
        n = conn.execute("SELECT COUNT(*) FROM briefs").fetchone()[0]
        conn.close()
        assert n == 1


class TestRuleSummary:
    def test_empty_day_message(self):
        s = {"todos": [], "replies": [], "notices": [], "resolved": []}
        text = brief_mod._rule_summary(s, "2026-11-17")
        assert "没有新工作事件" in text

    def test_summary_mentions_counts(self):
        s = {"todos": [{"text": "x" * 30, "group": "g"}], "replies": [],
             "notices": [], "resolved": []}
        text = brief_mod._rule_summary(s, "2026-11-17")
        assert "1 件待办" in text
