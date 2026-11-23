"""任务相关路由：/api/tasks CRUD、任务证据追溯、消息搜索。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from datetime import datetime, timezone
import re

from messagehub.config import CN_TZ
from messagehub.database import get_db

router = APIRouter()


class TaskUpdate(BaseModel):
    status: str


class WorkbenchItem(BaseModel):
    title: Optional[str] = None
    category: Optional[str] = None
    deadline: Optional[str] = None
    status: Optional[str] = None
    expected_updated_at: Optional[str] = None


_CATEGORIES = {'材料提交', '报名填写', '会议活动', '沟通确认', '其他'}
_STATUSES = {'pending', 'accepted', 'in_progress', 'completed', 'review', 'notice', 'ignored', 'dismissed', 'archived'}


def _context_summary(conn, task):
    """Show nearby fragments from the sender as evidence, without inferring intent."""
    from messagehub.task_pipeline import _time

    text = (task['text'] or '').strip()
    if not task['message_id'] or not re.fullmatch(r'@[^\s@，,。:：！!？?]+[\s，,。:：！!？?]*', text):
        return None
    anchor = _time(task['sent_at'])
    if not anchor:
        return None
    fields = 'id,sender_name,sent_at,text'
    after = conn.execute(f'''SELECT {fields} FROM messages WHERE conversation_id=? AND id>?
        ORDER BY id ASC LIMIT 4''', (task['conversation_id'], task['message_id'])).fetchall()
    before = conn.execute(f'''SELECT {fields} FROM messages WHERE conversation_id=? AND id<?
        ORDER BY id DESC LIMIT 4''', (task['conversation_id'], task['message_id'])).fetchall()

    def fragments(rows):
        result = []
        for row in rows:
            sent = _time(row['sent_at'])
            if row['sender_name'] != task['sender_name'] or not sent or abs((sent - anchor).total_seconds()) > 300:
                break
            fragment = ' '.join((row['text'] or '').split())
            if not fragment or fragment.startswith('@') or re.fullmatch(r'\[[^\]]+\]', fragment):
                break
            result.append(fragment)
        return result

    # Prefer follow-up text: it is commonly the question after a standalone @.
    parts = fragments(after)
    if not parts:
        parts = list(reversed(fragments(before)))
    return '；'.join(parts)[:180] or None


def _validated_item(item: WorkbenchItem, creating: bool = False):
    values = item.model_dump(exclude_unset=True, exclude={"expected_updated_at"})
    if creating or 'title' in values:
        title = (values.get('title') or '').strip()
        if not title or len(title) > 200:
            raise HTTPException(status_code=400, detail='标题应为 1 至 200 个字符')
        values['title'] = title
    if 'category' in values and values['category'] not in _CATEGORIES:
        raise HTTPException(status_code=400, detail='事项类型无效')
    if 'status' in values and values['status'] not in _STATUSES:
        raise HTTPException(status_code=400, detail='事项状态无效')
    if 'deadline' in values:
        deadline = values['deadline']
        if deadline:
            try:
                parsed = datetime.fromisoformat(deadline)
                values['deadline'] = (parsed if parsed.tzinfo else parsed.replace(tzinfo=CN_TZ)).isoformat()
            except ValueError:
                raise HTTPException(status_code=400, detail='截止时间无效')
        else:
            values['deadline'] = None
    return values


@router.get('/api/workbench/items')
async def workbench_items():
    conn = get_db()
    try:
        rows = conn.execute('SELECT * FROM personal_tasks ORDER BY id DESC').fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item['context_summary'] = _context_summary(conn, item)
            items.append(item)
        return {'items': items}
    finally:
        conn.close()


@router.get('/api/workbench/items/{task_id}/context')
async def workbench_context(task_id: int):
    conn = get_db()
    try:
        task = conn.execute('SELECT message_id, conversation_id FROM personal_tasks WHERE id=?', (task_id,)).fetchone()
        if not task:
            raise HTTPException(status_code=404, detail='事项不存在')
        if task['message_id'] is None:
            return {'messages': []}
        rows = conn.execute('''SELECT id,sender_name,sent_at,text FROM messages
            WHERE conversation_id=? AND id IN (
                SELECT id FROM messages WHERE conversation_id=? AND id<? ORDER BY id DESC LIMIT 5)
            UNION ALL SELECT id,sender_name,sent_at,text FROM messages
            WHERE conversation_id=? AND id=?
            UNION ALL SELECT id,sender_name,sent_at,text FROM messages
            WHERE conversation_id=? AND id IN (
                SELECT id FROM messages WHERE conversation_id=? AND id>? ORDER BY id ASC LIMIT 5)
            ORDER BY id''', (task['conversation_id'], task['conversation_id'], task['message_id'],
                             task['conversation_id'], task['message_id'], task['conversation_id'],
                             task['conversation_id'], task['message_id'])).fetchall()
        return {'messages': [dict(row) for row in rows]}
    finally:
        conn.close()


@router.post('/api/workbench/items', status_code=201)
async def create_workbench_item(item: WorkbenchItem):
    values = _validated_item(item, creating=True)
    now = datetime.now(CN_TZ).isoformat()
    conn = get_db()
    try:
        cur = conn.execute('''INSERT INTO personal_tasks
            (message_id, conversation_id, conversation_name, sender_name, sent_at, text,
             category, deadline, status, reason, updated_at, title)
            VALUES (NULL, '', '手动创建', '', ?, '', ?, ?, 'pending', '手动创建', ?, ?)''',
            (now, values.get('category') or '其他', values.get('deadline'), now, values['title']))
        conn.commit()
        return {'id': cur.lastrowid}
    finally:
        conn.close()


@router.patch('/api/workbench/items/{task_id}')
async def edit_workbench_item(task_id: int, item: WorkbenchItem):
    values = _validated_item(item)
    if not values:
        raise HTTPException(status_code=400, detail='没有需要更新的内容')
    values['updated_at'] = datetime.now(CN_TZ).isoformat()
    conn = get_db()
    try:
        assignments = ', '.join(f'{key}=?' for key in values)
        condition = 'id=?'
        parameters = (*values.values(), task_id)
        if item.expected_updated_at is not None:
            condition += ' AND updated_at=?'
            parameters += (item.expected_updated_at,)
        cur = conn.execute(f'UPDATE personal_tasks SET {assignments} WHERE {condition}', parameters)
        if not cur.rowcount:
            if conn.execute('SELECT 1 FROM personal_tasks WHERE id=?', (task_id,)).fetchone():
                raise HTTPException(409, '事项已更新，当前操作未生效')
            raise HTTPException(404, '事项不存在')
        conn.commit()
        return {'status': 'ok', 'updated_at': values['updated_at']}
    finally:
        conn.close()


@router.get("/api/personal-tasks/status")
async def personal_task_status():
    from messagehub.task_runtime import get_scheduler
    return JSONResponse(dict(get_scheduler().state))


@router.get("/api/personal-tasks")
async def personal_tasks(status: Optional[str] = None):
    conn = get_db()
    try:
        if status == "completed":
            rows = conn.execute("SELECT * FROM personal_tasks WHERE status='completed' ORDER BY updated_at DESC LIMIT 200")
        else:
            rows = conn.execute("SELECT * FROM personal_tasks WHERE status != 'completed' ORDER BY CASE status WHEN 'review' THEN 1 ELSE 0 END, CASE WHEN deadline IS NULL THEN 1 ELSE 0 END, deadline, id DESC")
        return JSONResponse({"tasks": [dict(r) for r in rows]})
    finally:
        conn.close()


@router.put("/api/personal-tasks/{task_id}")
async def update_personal_task(task_id: int, update: TaskUpdate):
    if update.status not in ('pending', 'accepted', 'in_progress', 'completed', 'review'):
        raise HTTPException(status_code=400, detail="任务状态无效")
    conn = get_db()
    try:
        cur = conn.execute("UPDATE personal_tasks SET status=?, updated_at=? WHERE id=?", (update.status, datetime.now(CN_TZ).isoformat(), task_id))
        conn.commit()
        if not cur.rowcount:
            raise HTTPException(status_code=404, detail="任务不存在")
        return JSONResponse({"status": "ok"})
    finally:
        conn.close()


@router.get("/api/tasks")
async def get_tasks(status: Optional[str] = None):
    """获取任务列表"""
    conn = get_db()
    cursor = conn.cursor()

    if status:
        cursor.execute("SELECT * FROM tasks WHERE status = ?", (status,))
    else:
        cursor.execute("SELECT * FROM tasks ORDER BY created_at DESC")

    tasks = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return JSONResponse({"tasks": tasks})


@router.put("/api/tasks/{task_id}")
async def update_task(task_id: int, update: TaskUpdate):
    """更新任务状态"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
        (update.status, datetime.now(CN_TZ).isoformat(), task_id)
    )
    conn.commit()
    conn.close()

    return JSONResponse({"status": "ok"})


@router.delete("/api/tasks/{task_id}")
async def delete_task(task_id: int):
    """删除任务"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()

    return JSONResponse({"status": "ok"})


@router.get("/api/tasks/{task_id}/evidence")
async def get_task_evidence(task_id: int):
    """获取任务对应的原始消息证据（原文追溯）。"""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT source_message_ids, evidence_text FROM tasks WHERE id=?", (task_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="任务不存在")
    ids = (row["source_message_ids"] or "").split(",")
    ids = [i for i in ids if i]
    evidence_text = row["evidence_text"]
    # 反查原始消息
    messages = []
    if ids:
        placeholders = ",".join("?" * len(ids))
        cur.execute(
            f"SELECT id, conversation_name, sender_name, sent_at, text FROM messages WHERE id IN ({placeholders})",
            ids,
        )
        messages = [dict(r) for r in cur.fetchall()]
    conn.close()
    return JSONResponse({
        "task_id": task_id,
        "evidence_text": evidence_text,
        "source_messages": messages,
    })


@router.get("/api/messages/search")
async def search_messages(q: str, platform: Optional[str] = None, limit: int = 50):
    """搜索消息"""
    conn = get_db()
    cursor = conn.cursor()

    query = "SELECT * FROM messages WHERE text LIKE ?"
    params = [f"%{q}%"]

    if platform:
        query += " AND source_platform = ?"
        params.append(platform)

    query += " ORDER BY sent_at DESC LIMIT ?"
    params.append(limit)

    cursor.execute(query, params)
    messages = [dict(row) for row in cursor.fetchall()]
    conn.close()

    return JSONResponse({"messages": messages})
