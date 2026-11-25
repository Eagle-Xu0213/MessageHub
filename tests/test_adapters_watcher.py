"""适配器 + 目录扫描测试（使用项目内临时目录避免沙箱 cleanup 限制）"""
import sys
import os
import shutil
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from messagehub.adapters.registry import (
    detect_and_parse, _to_iso
)
from messagehub.pipeline.importer import import_file
from messagehub.pipeline.watcher import scan_directory, get_latest_capture_status
from messagehub.app import get_db, init_db

DB_PATH = str(os.environ.get("MESSAGEHUB_DB_PATH") or (Path(__file__).parent.parent / "runtime" / "messagehub.db"))
TMP = Path(__file__).parent.parent / "runtime" / "test_tmp"


@pytest.fixture(autouse=True)
def reset():
    init_db()
    conn = get_db()
    cur = conn.cursor()
    for t in ("messages", "tasks", "capture_runs", "scan_state"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit()
    conn.close()
    if TMP.exists():
        shutil.rmtree(TMP)
    TMP.mkdir(parents=True, exist_ok=True)
    yield
    if TMP.exists():
        shutil.rmtree(TMP)


def test_to_iso_millis():
    iso, conf = _to_iso(1770571107000)
    assert conf == "exact"
    assert "T" in iso


def test_to_iso_chinese_datetime():
    iso, conf = _to_iso("2026-02-09 01:18:27")
    assert conf == "exact"
    assert "+08:00" in iso


def test_to_iso_empty():
    iso, conf = _to_iso("")
    assert iso == ""
    assert conf == "unknown"


def test_standard_jsonl_roundtrip():
    lines = [
        '{"source_platform":"wechat","conversation_id":"g1","conversation_name":"办公室","sender_name":"主任","sent_at":"2026-11-13T09:00:00+08:00","text":"提交总结","source_message_id":"m1"}',
        '{"source_platform":"wechat","conversation_id":"g1","conversation_name":"办公室","sender_name":"主任","sent_at":"2026-11-13T09:00:00+08:00","text":"提交总结","source_message_id":"m1"}',
    ]
    f = TMP / "std.jsonl"
    f.write_text("\n".join(lines), encoding="utf-8")
    raw, _ = detect_and_parse(str(f))
    assert len(raw) == 2
    res = import_file(DB_PATH, str(f))
    assert res["stats"]["inserted"] == 1  # 第二条因相同 id 指纹被跳过


def test_csv_import():
    f = TMP / "chat.csv"
    f.write_text("群名,发送者,时间,内容\n办公室,主任,2026-11-13 09:00:00,开会\n办公室,小李,2026-11-13 10:00:00,收到\n",
                 encoding="utf-8-sig")
    raw, _ = detect_and_parse(str(f))
    assert len(raw) == 2
    assert raw[0]["conversation_name"] == "办公室"


def test_watcher_incremental():
    watch = TMP / "exports"
    watch.mkdir()
    (watch / "a.jsonl").write_text(
        '{"source_platform":"wechat","conversation_id":"g","conversation_name":"G","sender_name":"s","sent_at":"2026-11-13T09:00:00+08:00","text":"消息1","source_message_id":"x1"}',
        encoding="utf-8")
    r1 = scan_directory(DB_PATH, str(watch))
    assert r1["status"] == "ok_new"
    assert r1["new"] == 1
    r2 = scan_directory(DB_PATH, str(watch))
    assert r2["status"] == "ok_no_new"
    assert r2["files_skipped"] == 1
    assert r2["new"] == 0
    (watch / "b.jsonl").write_text(
        '{"source_platform":"manual","conversation_id":"g2","conversation_name":"G2","sender_name":"s2","sent_at":"2026-11-13T10:00:00+08:00","text":"消息2","source_message_id":"y1"}',
        encoding="utf-8")
    r3 = scan_directory(DB_PATH, str(watch))
    assert r3["status"] == "ok_new"
    assert r3["new"] == 1
    statuses = get_latest_capture_status(DB_PATH)
    assert len(statuses) == 3
    assert any(s["status"] == "ok_new" for s in statuses)


def test_watcher_error_state():
    watch = TMP / "bad"
    watch.mkdir()
    f2 = watch / "broken.json"
    f2.write_text("{not valid json", encoding="utf-8")
    r2 = scan_directory(DB_PATH, str(watch))
    assert r2["status"] in ("error", "partial")
    assert r2["errors"]
