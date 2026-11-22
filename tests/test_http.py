"""P0 端到端 HTTP 测试：使用 starlette TestClient 验证 API 闭环"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from starlette.testclient import TestClient
from messagehub.app import app, get_db, init_db


@pytest.fixture(scope="function", autouse=True)
def reset_state():
    """清空数据库，保证测试独立"""
    init_db()
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM messages")
    cur.execute("DELETE FROM tasks")
    cur.execute("DELETE FROM import_batches")
    conn.commit()
    conn.close()
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def sample_file(tmp_path):
    f = tmp_path / "sample.jsonl"
    content = "\n".join([
        '{"source_platform":"wechat","source_method":"manual_import","conversation_id":"g1","conversation_name":"办公室群","sender_name":"主任","sent_at":"2026-11-13T09:00:00+08:00","text":"请今天17:00前提交总结"}',
        '{"source_platform":"qq","source_method":"manual_import","conversation_id":"g2","conversation_name":"志愿者群","sender_name":"组长","sent_at":"2026-11-13T10:00:00+08:00","text":"周六活动报名明天下午5点截止"}',
        '{"source_platform":"wechat","source_method":"manual_import","conversation_id":"g1","conversation_name":"办公室群","sender_name":"小李","sent_at":"2026-11-13T09:30:00+08:00","text":"收到，马上提交"}',
    ])
    f.write_text(content, encoding="utf-8")
    return f


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_index_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "MessageHub" in r.text


def test_import_jsonl(client, sample_file):
    with open(sample_file, "rb") as f:
        r = client.post(
            "/api/import",
            files={"file": ("sample.jsonl", f, "application/jsonl")},
        )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["inserted"] == 3
    assert data["duplicate"] == 0
    assert data["total"] == 3


def test_import_idempotent(client, sample_file):
    """同一文件连续导入 3 次，不膨胀"""
    for _ in range(3):
        with open(sample_file, "rb") as f:
            r = client.post(
                "/api/import",
                files={"file": ("sample.jsonl", f, "application/jsonl")},
            )
        assert r.status_code == 200
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM messages")
    count = cur.fetchone()[0]
    conn.close()
    assert count == 3


def test_import_malformed_no_crash(client, tmp_path):
    """畸形文件不破坏数据库"""
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"text":broken}\nnot json\n{"source_platform":"wechat","conversation_id":"x","conversation_name":"y","sender_name":"z","sent_at":"2026-11-13T00:00:00+08:00","text":"ok"}\n', encoding="utf-8")
    with open(bad, "rb") as f:
        r = client.post("/api/import", files={"file": ("bad.jsonl", f, "application/jsonl")})
    assert r.status_code == 200
    data = r.json()
    # 至少有效行被导入，无效行跳过，不报错
    assert data["inserted"] >= 1


def test_task_create_via_db_and_api(client, sample_file):
    """导入后手动插入任务，验证 API 读取/更新/删除"""
    # 导入消息
    with open(sample_file, "rb") as f:
        client.post("/api/import", files={"file": ("sample.jsonl", f, "application/jsonl")})
    # 直接插任务
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO tasks (title, description, category, my_role, deadline, status) VALUES (?,?,?,?,?,?)",
        ("提交总结", "主任要求今天17点前", "action", "我需要执行", "2026-11-13T17:00:00+08:00", "uncertain"),
    )
    conn.commit()
    conn.close()
    # 读取
    r = client.get("/api/tasks")
    assert r.status_code == 200
    tasks = r.json()["tasks"]
    assert len(tasks) == 1
    tid = tasks[0]["id"]
    # 更新状态
    r = client.put(f"/api/tasks/{tid}", json={"status": "completed"})
    assert r.status_code == 200
    r = client.get("/api/tasks?status=completed")
    assert len(r.json()["tasks"]) == 1
    # 删除
    r = client.delete(f"/api/tasks/{tid}")
    assert r.status_code == 200
    r = client.get("/api/tasks")
    assert len(r.json()["tasks"]) == 0


def test_search(client, sample_file):
    with open(sample_file, "rb") as f:
        client.post("/api/import", files={"file": ("sample.jsonl", f, "application/jsonl")})
    r = client.get("/api/messages/search", params={"q": "提交"})
    assert r.status_code == 200
    msgs = r.json()["messages"]
    assert len(msgs) >= 1
    assert any("提交" in m["text"] for m in msgs)


def test_removed_history_analysis_endpoints(client):
    assert client.get('/memories').status_code == 404
    assert client.get('/api/memories').status_code == 404
    assert client.post('/api/scope/ai-presel', json={'names': []}).status_code == 404
    assert client.post('/api/brief/reset').status_code == 404
    assert '去年今日' not in client.get('/').text
    assert 'AI 自动分级' not in client.get('/select').text


def test_update_requires_local_origin(client, monkeypatch):
    from messagehub import update
    called = []
    monkeypatch.setattr(update.sys, 'platform', 'win32')
    monkeypatch.setattr(update, 'start_update', lambda: called.append(True))
    assert client.post('/api/update').status_code == 403
    assert client.post('/api/update', headers={'Origin':'http://evil.example'}).status_code == 403
    assert client.post('/api/update', headers={'Origin':'http://127.0.0.1:8000','Sec-Fetch-Site':'same-origin'}).status_code == 200
    assert called == [True]
    assert '一键更新' in client.get('/').text
