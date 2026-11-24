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


def test_qq_scope_and_workbench_use_stable_conversation_ids(client, tmp_path, monkeypatch):
    import importlib
    import json
    from datetime import datetime, timedelta
    from messagehub import database, personal, task_pipeline
    from messagehub.config import CN_TZ

    config = tmp_path / "personal.json"
    config.write_text(json.dumps({"identity": {"nicknames": ["张三"]}}), encoding="utf-8")
    monkeypatch.setenv("MESSAGEHUB_PERSONAL_CONFIG", str(config))
    personal.reload()
    db_path = tmp_path / "qq.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(importlib.import_module("messagehub.app"), "DB_PATH", db_path)
    init_db()
    task_pipeline.initialize_database(db_path)

    def upload(kind, peer, messages, sent_at=None):
        timestamp = (sent_at or datetime.now(CN_TZ)).isoformat()
        export = {
            "chatInfo": {"name": "同名会话", "type": kind, "selfUin": "10001", "peerUid": peer},
            "messages": [
                {"id": sid, "timestamp": timestamp, "sender": {"uin": "10002", "name": "同事"},
                 "content": {"text": content}}
                for sid, content in messages
            ],
        }
        response = client.post("/api/import", files={"file": ("qq.json", json.dumps(export))})
        assert response.status_code == 200, response.text
        return response.json()

    try:
        historical = datetime.now(CN_TZ) - timedelta(days=1)
        upload("group", "20001", [("1", "@张三 请提交历史报告")], historical)
        upload("group", "20002", [("1", "历史消息")], historical)
        upload("private", "20001", [("1", "历史消息")], historical)
        data = client.get("/api/scope/conversations").json()
        qq = [row for row in data["conversations"] if row.get("platform") == "qq"]
        assert len(qq) == 3
        assert all(row["level"] is None for row in qq)
        chosen = {row["id"]: "work" for row in qq if row["kind"] == "contact" or row["identity_hint"].endswith("0001")}
        assert len(chosen) == 2
        response = client.post("/api/scope/config", json={"qq_levels": chosen})
        assert response.status_code == 200, response.text
        assert response.json()["levels"] == data["levels"]

        group = [("2", "请提交项目报告"), ("3", "请提交项目报告")]
        assert upload("group", "20001", group)["inserted"] == 2
        assert upload("group", "20001", group)["duplicate"] == 2
        upload("group", "20002", [("2", "@张三 请提交其他组报告")])
        upload("private", "20001", [("2", "请填写工作表格")])
        upload("group", "20001", [("4", "@张三 请提交较早的报告")], historical)
        with get_db() as conn:
            assert task_pipeline.process_batch(conn)["created"] == 3
        with get_db() as conn:
            assert task_pipeline.process_batch(conn)["created"] == 0

        items = client.get("/api/workbench/items").json()["items"]
        assert len(items) == 3
        assert {item["source_platform"] for item in items} == {"qq"}
        assert sorted(item["status"] for item in items) == ["pending", "review", "review"]
        private = next(item for item in items if item["status"] == "pending")
        context = client.get(f"/api/workbench/items/{private['id']}/context").json()["messages"]
        assert {row["text"] for row in context} == {"历史消息", "请填写工作表格"}
    finally:
        monkeypatch.undo()
        personal.reload()


def test_onebot_websocket_scope_and_tasks(tmp_path, monkeypatch):
    import json
    from datetime import datetime, timedelta
    from starlette.websockets import WebSocketDisconnect
    from messagehub import database, personal, task_pipeline
    from messagehub.config import CN_TZ
    from messagehub.routers import qq

    db_path = tmp_path / "qq.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(qq, "CONFIG_PATH", tmp_path / "onebot.json")
    monkeypatch.setattr(qq, "STATE_PATH", tmp_path / "receiver.json")
    monkeypatch.setattr(qq, "SUPERVISOR_PATH", tmp_path / "supervisor.json")
    monkeypatch.setattr(qq, "catalog", {})
    monkeypatch.setattr(qq, "_last_message_at", None)
    monkeypatch.setattr(qq, "_disconnected_at", None)
    monkeypatch.setattr(qq, "_disconnect_reason", None)
    account = "12345"
    headers = {"Authorization": "Bearer token-for-test"}
    endpoint = "/api/qq/onebot/ws"
    client = TestClient(app, client=("127.0.0.1", 54321))
    assert client.get("/api/qq/status").json()["state"] == "disabled"
    qq.CONFIG_PATH.write_text(json.dumps({"enabled": True, "account": account, "token": "token-for-test"}))
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(endpoint, headers={"Authorization": "Bearer wrong"}):
            pass

    init_db()
    task_pipeline.initialize_database(db_path)
    group = qq.conversation_id(account, "group", "20001")
    private = qq.conversation_id(account, "contact", "20002")
    since = datetime.now(CN_TZ) - timedelta(minutes=1)
    monkeypatch.setattr(personal, "_CONFIG", {
        **personal._CONFIG, "qq_scope": {
            key: {"level": "work", "since": since.isoformat(), "baseline_id": 0}
            for key in (group, private)
        }
    })

    def connect(ws):
        for action, data in (
            ("get_login_info", {"user_id": 12345}),
            ("get_group_list", [{"group_id": 20001, "group_name": "Test"}, {"group_id": 20003}]),
            ("get_friend_list", [{"user_id": 20002, "nickname": "Test"}]),
        ):
            assert ws.receive_json()["action"] == action
            ws.send_json({"echo": action, "status": "ok", "retcode": 0, "data": data})

    def send(ws, sid, **overrides):
        ws.send_json({
            "post_type": "message", "message_type": "group", "message_id": sid,
            "self_id": 12345, "user_id": 20002, "group_id": 20001,
            "time": int(datetime.now(CN_TZ).timestamp()) + 1,
            "message": [{"type": "at", "data": {"qq": "12345"}},
                        {"type": "text", "data": {"text": "请提交项目报告"}}],
            **overrides,
        })

    with client.websocket_connect(endpoint, headers=headers) as ws:
        connect(ws)
        ws.send_json({"post_type": "meta_event", "meta_event_type": "heartbeat", "self_id": 12345})
        send(ws, 1)
        send(ws, 1)
        send(ws, 2)
        send(ws, 3, self_id=67890)
        send(ws, 4, group_id=20003, time=int(datetime.now(CN_TZ).timestamp()) + 60)
        send(ws, 5, time=int(since.timestamp())-1)
        send(ws, 6, message_type="private", sub_type="group")
        send(ws, 7, message_type="private", sub_type="friend")
    status = client.get("/api/qq/status").json()
    assert status["state"] == "disconnected"
    assert status["last_message_at"] and status["disconnected_at"] and status["last_heartbeat_at"]
    assert status["disconnect_reason"] == "peer_closed"
    rows = [row for row in client.get("/api/scope/conversations").json()["conversations"]
            if row.get("platform") == "qq"]
    unselected = qq.conversation_id(account, "group", "20003")
    assert {row["id"] for row in rows} == {group, private, unselected}
    assert rows[0]["id"] == unselected and rows[0]["latest_id"] == 0

    with client.websocket_connect(endpoint, headers=headers) as ws:
        connect(ws)
        send(ws, 1)
        send(ws, 8)
    with get_db() as conn:
        assert conn.execute("SELECT count(*) FROM messages").fetchone()[0] == 4
        assert task_pipeline.process_batch(conn)["created"] == 4
    items = client.get("/api/workbench/items").json()["items"]
    assert len(items) == 4
    assert {(row["source_platform"], row["status"]) for row in items} == {("qq", "pending")}

    monkeypatch.setattr(qq, "IDLE_TIMEOUT", 0.2)
    with client.websocket_connect(endpoint, headers=headers) as ws:
        connect(ws)
        assert ws.receive()["code"] == 1013
    monkeypatch.setattr(qq, "_disconnected_at", None)
    monkeypatch.setattr(qq, "_disconnect_reason", None)
    monkeypatch.setattr(qq, "_last_message_at", None)
    persisted = client.get("/api/qq/status").json()
    assert persisted["state"] == "disconnected"
    assert persisted["disconnect_reason"] == "heartbeat_timeout"
    assert persisted["last_message_at"] == status["last_message_at"] or persisted["last_message_at"] > status["last_message_at"]
    assert persisted["last_heartbeat_at"] == status["last_heartbeat_at"]
