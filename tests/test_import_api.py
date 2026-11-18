"""采集模块（适配器 + 目录扫描 + API）集成测试（使用项目内临时目录避免沙箱 cleanup 限制）"""
import sys
import os
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from starlette.testclient import TestClient
from messagehub.app import app, get_db, init_db
from messagehub.pipeline.watcher import scan_directory

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


@pytest.fixture
def client():
    return TestClient(app)


def test_qq_exporter_api_ingest(client):
    d = TMP / "exports"
    d.mkdir()
    import json
    data = {
        "metadata": {"name": "QQChatExporter V5"},
        "chatInfo": {"name": "志愿者群", "selfUin": "10001"},
        "messages": [
            {"id": "1", "timestamp": 1770571107000, "sender": {"name": "组长"},
             "content": {"text": "周六报名明天截止"}},
        ],
    }
    (d / "qq.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    r = scan_directory(DB_PATH, str(d))
    assert r["new"] == 1
    resp = client.get("/api/messages/search", params={"q": "报名"})
    assert resp.status_code == 200
    assert len(resp.json()["messages"]) >= 1


def test_scan_endpoint(client):
    imports_dir = Path(os.environ.get("MESSAGEHUB_WATCH_DIR")
                       or (Path(__file__).parent.parent / "imports"))
    imports_dir.mkdir(exist_ok=True)
    f = imports_dir / "api_test.jsonl"
    f.write_text('{"source_platform":"wechat","conversation_id":"g","conversation_name":"G","sender_name":"s","sent_at":"2026-11-13T09:00:00+08:00","text":"API扫描测试","source_message_id":"api1"}',
                 encoding="utf-8")
    try:
        resp = client.post("/api/scan")
        assert resp.status_code == 200
        assert resp.json()["new"] >= 1
    finally:
        f.unlink(missing_ok=True)


def test_capture_status_endpoint(client):
    resp = client.get("/api/capture-status")
    assert resp.status_code == 200
    body = resp.json()
    assert "runs" in body
    assert "total_messages" in body
    assert "watch_dir" in body
