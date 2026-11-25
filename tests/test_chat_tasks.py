import json
from datetime import datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from messagehub import database, task_pipeline
from messagehub.llm.provider import LLMProvider
from messagehub.routers import tasks


ORIGIN = 'http://127.0.0.1:8000'
DRAFT = '/api/workbench/items/draft-from-messages'
ITEMS = '/api/workbench/items'
CATEGORIES = '/api/workbench/categories'
BATCH_CATEGORY = ITEMS + '/batch-category'


@pytest.fixture
def chat_client(tmp_path, monkeypatch):
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'chat.db')
    database.init_db()
    conn = database.get_db()
    try:
        task_pipeline.init_store(conn)
        start = datetime.fromisoformat('2026-11-25T09:00:00+08:00')
        for index in range(52):
            conn.execute("""INSERT INTO messages (source_platform, source_method,
                conversation_id, conversation_name, sender_name, sent_at, text)
                VALUES ('wechat', 'fixture', 'a', '项目群', '同事', ?, ?)""",
                ((start + timedelta(minutes=index)).isoformat(), f'普通消息 {index}'))
        for platform, conv, sent_at, body, kind in (
            ('wechat', 'b', '2026-11-25T12:00:00+08:00', '同名群内容', 'text'),
            ('manual', 'a', '2026-11-25T05:00:00Z', '同 ID 的导入内容', 'text'),
            ('wechat', 'a', '2026-11-25T09:02:00+08:00', '把更新后的报名表交一下', 'text'),
            ('wechat', 'a', '2026-11-25T09:51:00+08:00', '明天下午五点前', 'text'),
            ('wechat', 'a', '2026-11-25T09:51:00+08:00', '[图片]', 'image'),
            ('wechat', 'b', 'now', '无法确定时间的导入消息', 'text'),
        ):
            conn.execute("""INSERT INTO messages (source_platform, source_method, conversation_id,
                conversation_name, sender_name, sent_at, text, message_type)
                VALUES (?, 'fixture', ?, '项目群', '同事', ?, ?, ?)""",
                (platform, conv, sent_at, body, kind))
        conn.commit()
    finally:
        conn.close()
    api = FastAPI()
    api.include_router(tasks.router)
    return TestClient(api, base_url=ORIGIN, headers={'Origin': ORIGIN})


def mock_provider(monkeypatch, status, result, enabled=True):
    provider = LLMProvider('https://configured.invalid/v1', 'test-key', 'test-model', enabled=enabled)
    monkeypatch.setattr(LLMProvider, 'from_env', lambda: provider)
    calls = []
    real_client = httpx.Client

    def handle(request):
        calls.append((str(request.url), json.loads(request.content)))
        return httpx.Response(status, json={'choices': [{'message': {'content': json.dumps(result)}}]})

    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: real_client(
        transport=httpx.MockTransport(handle), **kwargs))
    return calls


def test_browse_same_name_chats_and_load_earlier_messages(chat_client):
    chats = chat_client.get('/api/messages/conversations').json()['conversations']
    assert [(chat['source_platform'], chat['conversation_id']) for chat in chats] == [
        ('manual', 'a'), ('wechat', 'b'), ('wechat', 'a')]
    assert all(chat['conversation_name'] == '项目群' for chat in chats)
    params = {'source_platform': 'wechat', 'conversation_id': 'a'}
    recent = chat_client.get('/api/messages/recent', params=params).json()
    assert len(recent['messages']) == 50
    assert [msg['id'] for msg in recent['messages'][-3:]] == [52, 56, 57]
    params['before_id'] = recent['before_id']
    older = chat_client.get('/api/messages/recent', params=params).json()
    assert [msg['id'] for msg in older['messages']] == [1, 2, 3, 55, 4]
    assert older['before_id'] is None
    assert chat_client.get('/api/messages/search', params={'q': '报名表'}).json()['messages'][0]['id'] == 55


def test_draft_save_and_trace_only_selected_messages(chat_client, monkeypatch):
    calls = mock_provider(monkeypatch, 200, {'task': {
        'title': '提交报名表', 'category': '材料提交', 'deadline': '2026-11-26T17:00'}})
    selection = {'message_ids': [56, 55]}
    assert chat_client.post(DRAFT, json=selection, headers={'Origin': 'https://example.invalid'}).status_code == 403
    result = chat_client.post(DRAFT, json=selection)
    assert result.status_code == 200
    draft = result.json()['draft']
    assert draft == {'title': '提交报名表', 'category': '材料提交', 'deadline': '2026-11-26T17:00'}
    url, request = calls[0]
    assert url == 'https://configured.invalid/v1/chat/completions'
    assert request['model'] == 'test-model'
    prompt = request['messages'][0]['content']
    messages = json.loads(prompt.split('所选消息（按时间排序）：')[1])
    assert [(msg['sent_at'], msg['text']) for msg in messages] == [
        ('2026-11-25T09:02:00+08:00', '把更新后的报名表交一下'),
        ('2026-11-25T09:51:00+08:00', '明天下午五点前'),
    ]
    assert chat_client.get(ITEMS).json()['items'] == []
    draft['title'] = '交更新后的报名表'
    saved = chat_client.post(ITEMS, json={**draft, **selection})
    assert saved.status_code == 201
    task_id = saved.json()['id']
    repeated = chat_client.post(ITEMS, json={**draft, 'message_ids': [55, 56, 55]})
    assert repeated.json() == {'id': task_id, 'existing': True}
    item, = chat_client.get(ITEMS).json()['items']
    assert (item['title'], item['status'], item['source_platform']) == (draft['title'], 'pending', 'wechat')
    assert item['deadline'] == '2026-11-26T17:00:00+08:00'
    evidence = chat_client.get(f'{ITEMS}/{task_id}/context').json()['messages']
    assert [msg['id'] for msg in evidence] == [55, 56]
    assert len(calls) == 1


def test_draft_validation_and_provider_failures(chat_client, monkeypatch):
    for ids in ([54, 55], [53, 55], [55, 57], [55, 5000]):
        assert chat_client.post(DRAFT, json={'message_ids': ids}).status_code == 400
    assert chat_client.post(DRAFT, json={'message_ids': list(range(1, 22))}).status_code == 422
    selection = {'message_ids': [55]}
    with monkeypatch.context() as patch:
        calls = mock_provider(patch, 200, {'task': None}, enabled=False)
        assert chat_client.post(DRAFT, json=selection).json()['code'] == 'ai_disabled'
        assert not calls
    with monkeypatch.context() as patch:
        calls = mock_provider(patch, 200, {'task': None})
        assert chat_client.post(DRAFT, json=selection).json() == {'draft': None}
        assert len(calls) == 1
    with monkeypatch.context() as patch:
        mock_provider(patch, 200, {'task': {'title': '交材料', 'deadline': '2026-02-31T17:00'}})
        assert chat_client.post(DRAFT, json=selection).json()['draft']['deadline'] is None
    calls = mock_provider(monkeypatch, 429, {})
    assert chat_client.post(DRAFT, json=selection).status_code == 502
    assert [url for url, _ in calls] == ['https://configured.invalid/v1/chat/completions']
    assert chat_client.get(ITEMS).json()['items'] == []


def test_custom_categories_rename_delete_and_batch(chat_client):
    category = chat_client.post(CATEGORIES, json={'name': '  项目跟进  '})
    assert category.status_code == 201
    category_url = f"{CATEGORIES}/{category.json()['id']}"
    assert category.json()['name'] == '项目跟进'
    assert chat_client.post(CATEGORIES, json={'name': '项目跟进'}).status_code == 409
    assert chat_client.post(CATEGORIES, json={'name': '   '}).status_code == 400
    one = chat_client.post(ITEMS, json={'title': '任务一', 'category': '项目跟进'}).json()['id']
    two = chat_client.post(ITEMS, json={'title': '任务二', 'category': '报名填写'}).json()['id']
    three = chat_client.post(ITEMS, json={'title': '任务三'}).json()['id']
    assert chat_client.patch(f'{ITEMS}/{one}', json={'status': 'completed'}).status_code == 200
    assert chat_client.put(category_url, json={'name': '跟踪'}).status_code == 200

    def items():
        return {entry['id']: (entry['category'], entry['status'])
                for entry in chat_client.get(ITEMS).json()['items']}

    assert items()[one] == ('跟踪', 'completed')
    assert chat_client.patch(BATCH_CATEGORY, json={'task_ids': [two, 999], 'category': '跟踪'}).status_code == 409
    assert items()[two] == ('报名填写', 'pending')
    assert chat_client.patch(BATCH_CATEGORY, json={'task_ids': [one, two, two], 'category': '跟踪'}).json() == {'updated': 2}
    assert items() == {one: ('跟踪', 'completed'), two: ('跟踪', 'pending'), three: ('', 'pending')}
    assert chat_client.patch(BATCH_CATEGORY, json={'task_ids': [two], 'category': None}).json() == {'updated': 1}
    entry = next(c for c in chat_client.get(CATEGORIES).json()['categories'] if c['name'] == '跟踪')
    assert entry['task_count'] == 1
    assert chat_client.delete(category_url).json() == {'updated': 1}
    assert items()[one] == ('', 'completed')
    assert chat_client.patch(BATCH_CATEGORY, json={'task_ids': [two], 'category': '跟踪'}).status_code == 409


def test_ai_uses_custom_labels_and_rejects_stale_draft(chat_client, monkeypatch):
    custom = chat_client.post(CATEGORIES, json={'name': '项目跟进'}).json()
    selection = {'message_ids': [55]}
    with monkeypatch.context() as patch:
        calls = mock_provider(patch, 200, {'task': {'title': '交材料', 'category': '项目跟进'}})
        draft = chat_client.post(DRAFT, json=selection).json()['draft']
        assert draft['category'] == '项目跟进'
        assert '项目跟进' in calls[0][1]['messages'][0]['content']
    chat_client.delete(f"{CATEGORIES}/{custom['id']}")
    assert chat_client.post(ITEMS, json={**draft, **selection}).status_code == 409
    mock_provider(monkeypatch, 200, {'task': {'title': '交材料', 'category': '项目跟进'}})
    assert chat_client.post(DRAFT, json=selection).json()['draft']['category'] is None
