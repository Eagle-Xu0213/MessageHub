from fastapi.testclient import TestClient

from messagehub.app import app
from test_chat_tasks import chat_client


def test_dsh_views_share_workbench_items(chat_client):
    base = 'http://127.0.0.1:8000'
    full = TestClient(app, base_url=base)
    sidebar = TestClient(app, base_url=base)
    for path, client, mode in (('/', full, ''), ('/?embed=dsh', full, 'full'),
                               ('/dsh/todos?embed=dsh', sidebar, 'todos')):
        page = client.get(path)
        assert page.status_code == 200
        assert f'data-dsh-mode="{mode}"' in page.text
        assert ('id="dsh-switch"' in page.text) == bool(mode)
        assert ('/static/dsh-workbench.css' in page.text) == (mode == 'full')

    items = '/api/workbench/items'
    created = full.post(items, json={'title': 'DSH integration check'})
    assert created.status_code == 201
    task_id = created.json()['id']
    path = f'{items}/{task_id}'

    def task_from(client):
        return next(item for item in client.get(items).json()['items'] if item['id'] == task_id)

    assert task_from(sidebar)['title'] == 'DSH integration check'
    assert full.patch(path, json={'title': 'Updated in full workbench'}).status_code == 200
    assert task_from(sidebar)['title'] == 'Updated in full workbench'
    assert sidebar.get(path + '/context').json() == {'messages': []}
    assert sidebar.patch(path, json={'status': 'completed'}).status_code == 200
    assert task_from(full)['status'] == 'completed'
