"""Isolated coverage for incremental personal-task classification."""
import sqlite3
from datetime import datetime, timedelta

from messagehub import personal
from messagehub.config import CN_TZ
from messagehub.task_pipeline import classify, deadline_of, init_store, process_batch


def setup_db():
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY, conversation_id TEXT, conversation_name TEXT, sender_name TEXT, sent_at TEXT, text TEXT)')
    init_store(db)
    return db


def add(db, i, sender, text):
    db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)', (i, 'group-1', '工作群甲', sender, datetime.now(CN_TZ).isoformat(), text))
    db.commit()


def test_classification_and_no_generic_notices():
    base = {'conversation_name': '工作群甲', 'sender_name': '同事', 'sent_at': datetime.now(CN_TZ).isoformat()}
    assert classify(dict(base, text='@张三 请提交报名表'))['status'] == 'pending'
    assert classify(dict(base, text='@张三 会议安排'))['status'] == 'review'
    assert classify(dict(base, text='@全体成员 请提交材料'))['status'] == 'notice'
    assert classify(dict(base, text='@李四 请提交材料')) is None


def test_progress_and_explicit_completion():
    db = setup_db()
    name = next(iter(personal.MY_SENDER_NAMES))
    add(db, 1, '同事', '@张三 请提交报名表')
    add(db, 2, name, '收到')
    process_batch(db)
    assert db.execute('SELECT status FROM personal_tasks').fetchone()[0] == 'accepted'
    add(db, 3, name, '报名表已经提交了')
    process_batch(db)
    assert db.execute('SELECT status,completion_message_id FROM personal_tasks').fetchone()[:] == ('completed', 3)
    assert process_batch(db)['processed'] == 0


def test_deadline_is_relative_to_message():
    base = '2026-11-21T10:00:00+08:00'
    assert deadline_of('明天下午3点前提交', base) == '2026-11-22T15:00:00+08:00'
    assert deadline_of('明天下午三点半前提交', base) == '2026-11-22T15:30:00+08:00'
    assert deadline_of('下周一前提交', base) == '2026-11-23T23:59:00+08:00'
    assert deadline_of('2026-11-22 14:30 前提交', base) == '2026-11-22T14:30:00+08:00'
    assert deadline_of('明天开会', base) is None


def test_alert_channels_retry_independently():
    from messagehub.task_pipeline import send_due_alerts
    db = setup_db()
    add(db, 1, '同事', '@张三 请明天前提交报名表')
    process_batch(db)
    calls = []
    def mail(event):
        calls.append('mail')
        return {'sent': calls.count('mail') > 1}
    def desktop(event):
        calls.append('desktop')
        return True
    now = datetime.now(CN_TZ)
    db.execute('UPDATE personal_tasks SET deadline=?', ((now + timedelta(hours=1)).isoformat(),))
    db.commit()
    assert send_due_alerts(db, mail, desktop, now, retry_seconds=0) == {'urgent': 1, 'mail': 0, 'desktop': 1}
    assert send_due_alerts(db, mail, desktop, now, retry_seconds=0) == {'urgent': 1, 'mail': 1, 'desktop': 0}
    assert calls == ['mail', 'desktop', 'mail']


def test_baseline_and_late_delivery_are_distinguished():
    db = sqlite3.connect(':memory:')
    db.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY, conversation_id TEXT, conversation_name TEXT, sender_name TEXT, sent_at TEXT, text TEXT)')
    add(db, 1, '同事', '@张三 请提交报名表')
    init_store(db)
    add(db, 2, '同事', '@张三 请提交申请表')
    assert process_batch(db)['created'] == 1
    assert db.execute('SELECT message_id FROM personal_tasks').fetchone()[0] == 2


def test_completion_does_not_close_multiple_possible_tasks():
    db = setup_db()
    name = next(iter(personal.MY_SENDER_NAMES))
    add(db, 1, '同事', '@张三 请提交报名表')
    add(db, 2, '同事', '@张三 请提交报名表和材料')
    add(db, 3, name, '报名表已经提交了')
    process_batch(db)
    assert [r[0] for r in db.execute('SELECT status FROM personal_tasks ORDER BY id')] == ['pending', 'pending']


def test_digest_tracks_current_tasks_and_excludes_completed():
    from messagehub.task_pipeline import send_current_digest
    db = setup_db()
    add(db, 1, '同事', '@张三 请提交报名表')
    process_batch(db)
    payloads = []
    def send(payload):
        payloads.append(payload)
        return {'sent': True}
    assert send_current_digest(db, send)['open'] == 1
    assert send_current_digest(db, send)['open'] == 1
    db.execute("UPDATE personal_tasks SET status='completed'")
    db.commit()
    assert send_current_digest(db, send)['sent'] is False
    assert len(payloads) == 2


def test_negated_completion_does_not_close():
    db = setup_db()
    name = next(iter(personal.MY_SENDER_NAMES))
    add(db, 1, '同事', '@张三 请提交报名表')
    add(db, 2, name, '报名表还没有提交了')
    process_batch(db)
    assert db.execute('SELECT status FROM personal_tasks').fetchone()[0] == 'pending'


def test_urgent_boundary_and_completed_tasks():
    from messagehub.task_pipeline import send_due_alerts
    db = setup_db()
    add(db, 1, '同事', '@张三 请提交报名表')
    process_batch(db)
    now = datetime.now(CN_TZ)
    send = lambda event: True
    db.execute('UPDATE personal_tasks SET deadline=?', ((now + timedelta(hours=24, seconds=1)).isoformat(),))
    db.commit()
    assert send_due_alerts(db, send, send, now)['urgent'] == 0
    db.execute('UPDATE personal_tasks SET deadline=?', ((now + timedelta(hours=24)).isoformat(),))
    db.commit()
    assert send_due_alerts(db, send, send, now)['urgent'] == 1
    db.execute("UPDATE personal_tasks SET status='completed'")
    db.commit()
    assert send_due_alerts(db, send, send, now)['urgent'] == 0


def test_initialization_makes_backup_and_is_idempotent(tmp_path):
    from messagehub.task_pipeline import initialize_database
    path = tmp_path / 'messages.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY, text TEXT)')
        db.execute("INSERT INTO messages VALUES (1, 'keep')")
    initialize_database(path)
    backups = list(tmp_path.glob('messages.db.before-personal-tasks-*.bak'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as saved:
        assert saved.execute('SELECT text FROM messages').fetchone()[0] == 'keep'
        assert saved.execute("SELECT name FROM sqlite_master WHERE name='personal_tasks'").fetchone() is None
    initialize_database(path)
    assert len(list(tmp_path.glob('*.bak'))) == 1


def test_snapshot_readiness_requires_new_completed_generation(tmp_path):
    import json
    from messagehub.task_runtime import TaskScheduler
    p = tmp_path / 'capture.json'
    s = TaskScheduler(db_path=tmp_path / 'messages.db', state_path=p)
    fresh = (s.started_at + timedelta(seconds=1)).isoformat()
    p.write_text(json.dumps({'snapshot_completed_at': fresh, 'last_stats': {'drained': True}}))
    assert not s.snapshot_ready()
    p.write_text(json.dumps({'processed_generation': 'abc', 'snapshot_completed_at': fresh, 'last_stats': {'drained': False}}))
    assert not s.snapshot_ready()
    p.write_text(json.dumps({'processed_generation': 'abc', 'snapshot_completed_at': fresh, 'last_stats': {'drained': True}}))
    assert s.snapshot_ready()
    p.write_text(json.dumps({'processed_generation': 'abc', 'snapshot_completed_at': (s.started_at-timedelta(seconds=1)).isoformat(), 'last_stats': {'drained': True}}))
    assert not s.snapshot_ready()


def test_bound_id_survives_group_rename_and_prevents_name_collision(monkeypatch):
    from messagehub.task_pipeline import selected_group
    monkeypatch.setattr(personal, 'WORK_GROUPS', {'工作群甲'})
    monkeypatch.setattr(personal, 'WATCH_GROUPS', set())
    monkeypatch.setattr(personal, 'EXCLUDED_GROUPS', set())
    monkeypatch.setattr(personal, 'WORK_GROUP_IDS', {'one@chatroom'})
    monkeypatch.setattr(personal, 'WATCH_GROUP_IDS', set())
    monkeypatch.setattr(personal, 'EXCLUDED_GROUP_IDS', set())
    monkeypatch.setattr(personal, '_CONFIG', {'group_ids': {'work': {'one@chatroom': '工作群甲'}}})
    assert selected_group({'conversation_id': 'one@chatroom', 'conversation_name': '新群名'})
    assert not selected_group({'conversation_id': 'two@chatroom', 'conversation_name': '工作群甲'})


def test_task_api_lists_and_updates_independent_database(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from messagehub.routers import tasks
    path = tmp_path / 'api.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY, conversation_id TEXT, conversation_name TEXT, sender_name TEXT, sent_at TEXT, text TEXT)')
        init_store(db)
        add(db, 1, '同事', '@张三 请提交报名表')
        process_batch(db)
    def get_test_db():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn
    monkeypatch.setattr(tasks, 'get_db', get_test_db)
    api = FastAPI()
    api.include_router(tasks.router)
    client = TestClient(api)
    task = client.get('/api/personal-tasks').json()['tasks'][0]
    assert client.put('/api/personal-tasks/%d' % task['id'], json={'status':'completed'}).status_code == 200
    assert client.get('/api/personal-tasks').json()['tasks'] == []
    assert len(client.get('/api/personal-tasks?status=completed').json()['tasks']) == 1
    assert client.put('/api/personal-tasks/%d' % task['id'], json={'status':'pending'}).status_code == 200


def test_simultaneous_urgent_tasks_share_desktop_notification():
    from messagehub.task_pipeline import send_due_alerts
    db = setup_db()
    add(db, 1, '同事', '@张三 请提交报名表')
    add(db, 2, '同事', '@张三 请提交申请表')
    process_batch(db)
    now = datetime.now(CN_TZ)
    db.execute('UPDATE personal_tasks SET deadline=?', ((now + timedelta(hours=1)).isoformat(),))
    db.commit()
    notices = []
    out = send_due_alerts(db, lambda ev: {'sent': True}, lambda ev: notices.append(ev) or True, now)
    assert out == {'urgent': 2, 'mail': 2, 'desktop': 2}
    assert len(notices) == 1 and '2 项任务' in notices[0]['text']
    assert send_due_alerts(db, lambda ev: True, lambda ev: notices.append(ev) or True, now)['desktop'] == 0


def test_scheduler_processes_without_sending_mail(tmp_path, monkeypatch):
    import json
    import time
    from messagehub import task_runtime
    path = tmp_path / 'messages.db'
    db = sqlite3.connect(path)
    db.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY, conversation_id TEXT, conversation_name TEXT, sender_name TEXT, sent_at TEXT, text TEXT)')
    init_store(db)
    add(db, 1, '同事', '@张三 请提交报名表')
    db.close()
    capture = tmp_path / 'capture.json'
    sent = []
    monkeypatch.setattr(task_runtime.mail, 'send_digest_email', lambda payload: sent.append(payload) or {'sent': True})
    monkeypatch.setattr(task_runtime.mail, 'send_urgent_email', lambda payload: {'sent': True})
    monkeypatch.setattr(task_runtime.desktop_notify, 'notify_urgent', lambda payload: True)
    scheduler = task_runtime.TaskScheduler(path, capture, poll_seconds=.02, hourly_seconds=60)
    scheduler.start()
    try:
        time.sleep(.08)
        assert not sent
        capture.write_text(json.dumps({'processed_generation': 'a',
            'snapshot_completed_at': (scheduler.started_at + timedelta(seconds=1)).isoformat(),
            'last_stats': {'drained': True}}), encoding='utf-8')
        time.sleep(.12)
        assert not sent
        assert scheduler.state['capture'] == '补扫完成'
        with sqlite3.connect(path) as check:
            assert check.execute('SELECT COUNT(*) FROM personal_tasks').fetchone()[0] == 1
    finally:
        scheduler.stop()


def test_completion_does_not_mix_separate_clauses():
    db = setup_db()
    name = next(iter(personal.MY_SENDER_NAMES))
    add(db, 1, '同事', '@张三 请提交报名表')
    add(db, 2, name, '报名表明天提交，工作报告已经提交了')
    process_batch(db)
    assert db.execute('SELECT status FROM personal_tasks').fetchone()[0] == 'pending'


def test_workbench_create_edit_and_preserve_source(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from messagehub.routers import tasks
    path = tmp_path / 'workbench.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY, conversation_id TEXT, conversation_name TEXT, sender_name TEXT, sent_at TEXT, text TEXT)')
        init_store(db)
        add(db, 1, '同事', '@张三 请提交报名表')
        add(db, 2, '同事', '通知：大家明天开会')
        process_batch(db)
    def connection():
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        return db
    monkeypatch.setattr(tasks, 'get_db', connection)
    api = FastAPI()
    api.include_router(tasks.router)
    client = TestClient(api)
    original = client.get('/api/workbench/items').json()['items']
    notice = next(x for x in original if x['status'] == 'notice')
    task = next(x for x in original if x['status'] == 'pending')
    assert client.patch(f"/api/workbench/items/{notice['id']}", json={'status':'archived'}).status_code == 200
    assert client.patch(f"/api/workbench/items/{notice['id']}", json={'status':'pending'}).status_code == 200
    assert client.patch(f"/api/workbench/items/{task['id']}", json={'title':'新标题','category':'其他','deadline':'2026-12-01T12:00'}).status_code == 200
    assert client.post('/api/workbench/items', json={'title':'手动事项','category':'其他'}).status_code == 201
    assert client.post('/api/workbench/items', json={'title':''}).status_code == 400
    result = client.get('/api/workbench/items').json()['items']
    updated = next(x for x in result if x['id'] == task['id'])
    assert updated['title'] == '新标题' and updated['text'] == task['text']
    assert any(x['title'] == '手动事项' and x['message_id'] is None for x in result)


def test_existing_tasks_migrate_with_backup(tmp_path):
    from messagehub.task_pipeline import initialize_database
    path = tmp_path / 'old.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY)')
        db.execute('CREATE TABLE task_pipeline_state (name TEXT PRIMARY KEY, value TEXT NOT NULL)')
        db.execute("INSERT INTO task_pipeline_state VALUES ('cursor','5')")
        db.execute("""CREATE TABLE personal_tasks (id INTEGER PRIMARY KEY, message_id INTEGER NOT NULL UNIQUE,
            conversation_id TEXT NOT NULL, conversation_name TEXT NOT NULL, sender_name TEXT NOT NULL,
            sent_at TEXT NOT NULL, text TEXT NOT NULL, category TEXT NOT NULL, deadline TEXT,
            status TEXT NOT NULL, reason TEXT NOT NULL, completion_message_id INTEGER, updated_at TEXT NOT NULL)""")
        db.execute("INSERT INTO personal_tasks VALUES (1, 5, 'g', '群', '人', '2026-01-01', '原文', '会议安排', NULL, 'pending', '理由', NULL, '2026-01-01')")
    initialize_database(path)
    assert len(list(tmp_path.glob('*.bak'))) == 1
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT text,category,status FROM personal_tasks').fetchone() == ('原文','会议活动','pending')
        assert db.execute("SELECT value FROM task_pipeline_state WHERE name='cursor'").fetchone()[0] == '5'
        assert next(x for x in db.execute('PRAGMA table_info(personal_tasks)') if x[1]=='message_id')[3] == 0
    initialize_database(path)
    assert len(list(tmp_path.glob('*.bak'))) == 1


def test_separate_mention_uses_adjacent_instruction_before_and_after():
    db = setup_db()
    add(db, 1, '同事', '请明天下午三点前提交项目报告')
    add(db, 2, '同事', '@张三')
    process_batch(db)
    row = db.execute('SELECT text,title,status,deadline FROM personal_tasks WHERE message_id=2').fetchone()
    assert row['text'] == '@张三' and row['title'] == '请明天下午三点前提交项目报告'
    assert row['status'] == 'pending' and row['deadline'] is not None
    add(db, 3, '同事', '@张三')
    process_batch(db)
    add(db, 4, '同事', '请填写活动报名表')
    process_batch(db)
    row = db.execute('SELECT text,title,status FROM personal_tasks WHERE message_id=3').fetchone()
    assert tuple(row) == ('@张三', '请填写活动报名表', 'pending')


def test_context_does_not_cross_other_speakers_or_overwrite_user_choice():
    db = setup_db()
    add(db, 1, '甲', '请提交项目报告')
    add(db, 2, '乙', '@张三')
    process_batch(db)
    assert db.execute('SELECT status FROM personal_tasks WHERE message_id=2').fetchone() is None
    db.execute("INSERT INTO personal_tasks (message_id,conversation_id,conversation_name,sender_name,sent_at,text,category,status,reason,updated_at) SELECT id,conversation_id,conversation_name,sender_name,sent_at,text,'其他','review','提及本人，行动不明确',sent_at FROM messages WHERE id=2")
    db.execute("UPDATE personal_tasks SET status='ignored' WHERE message_id=2")
    db.commit()
    add(db, 3, '乙', '请填写报名表')
    process_batch(db)
    assert db.execute('SELECT status FROM personal_tasks WHERE message_id=2').fetchone()[0] == 'ignored'


def test_workbench_context_is_bound_to_conversation(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from messagehub.routers import tasks
    path = tmp_path / 'context.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY, conversation_id TEXT, conversation_name TEXT, sender_name TEXT, sent_at TEXT, text TEXT)')
        init_store(db)
        add(db, 1, '同事', '请提交报告')
        add(db, 2, '同事', '@张三')
        db.execute("INSERT INTO messages VALUES (3, 'other', '工作群甲', '别人', ?, '其他群内容')", (datetime.now(CN_TZ).isoformat(),))
        db.commit()
        process_batch(db)
    def connection():
        db = sqlite3.connect(path)
        db.row_factory = sqlite3.Row
        return db
    monkeypatch.setattr(tasks, 'get_db', connection)
    api = FastAPI(); api.include_router(tasks.router)
    client = TestClient(api)
    item = client.get('/api/workbench/items').json()['items'][0]
    messages = client.get(f"/api/workbench/items/{item['id']}/context").json()['messages']
    assert [x['text'] for x in messages] == ['请提交报告', '@张三']


def test_workbench_summary_uses_followup_question_for_existing_mention(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from messagehub.routers import tasks
    path = tmp_path / 'summary.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE messages (id INTEGER PRIMARY KEY, conversation_id TEXT, conversation_name TEXT, sender_name TEXT, sent_at TEXT, text TEXT)')
        init_store(db)
        add(db, 1, '同事', '@张三')
        process_batch(db)
        add(db, 2, '同事', '上次的工时还会加吗')
        db.execute("INSERT INTO personal_tasks (message_id,conversation_id,conversation_name,sender_name,sent_at,text,category,status,reason,updated_at) SELECT id,conversation_id,conversation_name,sender_name,sent_at,text,'其他','review','提及本人，行动不明确',sent_at FROM messages WHERE id=1")
        db.commit()
        add(db, 3, '其他人', '无关消息')
        add(db, 4, '同事', '不属于此轮')
    def connection():
        db = sqlite3.connect(path); db.row_factory = sqlite3.Row; return db
    monkeypatch.setattr(tasks, 'get_db', connection)
    api = FastAPI(); api.include_router(tasks.router)
    client = TestClient(api)
    item = client.get('/api/workbench/items').json()['items'][0]
    assert item['context_summary'] == '上次的工时还会加吗'
    assert item['text'] == '@张三' and item['title'] is None and item['status'] == 'review'
    assert client.patch(f"/api/workbench/items/{item['id']}", json={'title':'自己写的标题'}).status_code == 200
    assert client.get('/api/workbench/items').json()['items'][0]['title'] == '自己写的标题'


def test_work_and_mixed_filter_only_new_work_signals(monkeypatch):
    monkeypatch.setattr(personal, 'WORK_GROUPS', {'工作群甲'})
    monkeypatch.setattr(personal, 'WATCH_GROUPS', {'混杂群甲'})
    monkeypatch.setattr(personal, 'EXCLUDED_GROUPS', {'闲聊群'})
    monkeypatch.setattr(personal, 'WORK_GROUP_IDS', set())
    monkeypatch.setattr(personal, 'WATCH_GROUP_IDS', set())
    monkeypatch.setattr(personal, 'EXCLUDED_GROUP_IDS', set())
    monkeypatch.setattr(personal, '_CONFIG', {'group_ids': {}})
    db = setup_db()
    now = datetime.now(CN_TZ).isoformat()
    for i, group, msg in [(1,'工作群甲','@张三 吃饭了吗'),(2,'混杂群甲','@张三 你好'),(3,'混杂群甲','@张三 请提交材料'),(4,'闲聊群','@张三 请提交材料')]:
        db.execute('INSERT INTO messages VALUES (?,?,?,?,?,?)',(i,group,group,'同事',now,msg))
    db.commit(); process_batch(db)
    assert [(x[0],x[1]) for x in db.execute('SELECT message_id,status FROM personal_tasks')] == [(3,'pending')]


def test_mixed_group_bare_mention_promoted_only_after_work_context(monkeypatch):
    monkeypatch.setattr(personal, 'WATCH_GROUPS', {'工作群甲'})
    monkeypatch.setattr(personal, 'WORK_GROUPS', set())
    db = setup_db()
    add(db,1,'同事','@张三'); process_batch(db)
    assert db.execute('SELECT count(*) FROM personal_tasks').fetchone()[0] == 0
    add(db,2,'同事','上次少加的工时还会加吗'); process_batch(db)
    row = db.execute('SELECT message_id,title,status FROM personal_tasks').fetchone()
    assert tuple(row) == (1,'上次少加的工时还会加吗','review')


def test_selected_contacts_require_work_signal(monkeypatch):
    monkeypatch.setattr(personal, 'WORK_CONTACTS', {'工作联系人'})
    monkeypatch.setattr(personal, 'WATCH_CONTACTS', {'混合联系人'})
    monkeypatch.setattr(personal, 'EXCLUDED_CONTACTS', {'不检测联系人'})
    monkeypatch.setattr(personal, 'WORK_GROUPS', set())
    monkeypatch.setattr(personal, 'WATCH_GROUPS', set())
    monkeypatch.setattr(personal, 'EXCLUDED_GROUPS', set())
    for contact in ('工作联系人','混合联系人'):
        msg = {'conversation_name':contact,'conversation_id':'wxid_test','sender_name':contact,'sent_at':datetime.now(CN_TZ).isoformat()}
        assert classify(dict(msg,text='请吃饭')) is None
        assert classify(dict(msg,text='请提交项目材料'))['status'] == 'pending'
    assert classify({'conversation_name':'不检测联系人','conversation_id':'wxid_other','sender_name':'不检测联系人','sent_at':datetime.now(CN_TZ).isoformat(),'text':'请提交材料'}) is None
