"""任务相关路由：/api/tasks CRUD、任务证据追溯、消息搜索。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from datetime import datetime, timezone

from messagehub.config import CN_TZ
from messagehub.database import get_db

router = APIRouter()


class TaskUpdate(BaseModel):
    status: str


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
