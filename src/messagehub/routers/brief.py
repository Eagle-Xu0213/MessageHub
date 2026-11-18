"""简报路由（R3）：/brief 页面 + /api/brief 数据接口。

数据来源：briefs 表（R2 生成落库）；无当日简报时现场生成（days=1）。
事件溯源：前端拿 message_id 调 /api/brief/event/{id}/context 查原消息及前后文。

并发注意：generate_brief 含同步 LLM 调用（可长达数十秒），绝不能在
async def 路由里直接跑——会阻塞事件循环拖死所有请求（2026-11-17 实际
发生：regenerate 触发降级链重试，整站无响应）。统一走 anyio 线程池。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import timedelta
from typing import Any, Dict, List, Optional

import anyio
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader

from messagehub.brief import generate_brief, get_baseline
from messagehub.config import CN_TZ, DB_PATH, TEMPLATES_DIR, now_cn_iso
from messagehub.database import get_db

router = APIRouter()
_templates = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))


def _latest_brief(conn: sqlite3.Connection, date: Optional[str]) -> Optional[Dict[str, Any]]:
    if date:
        row = conn.execute(
            "SELECT id, date, generated_at, summary, event_count, degraded "
            "FROM briefs WHERE date=?", (date,)).fetchone()
    else:
        row = conn.execute(
            "SELECT id, date, generated_at, summary, event_count, degraded "
            "FROM briefs ORDER BY date DESC LIMIT 1").fetchone()
    if not row:
        return None
    brief = json.loads(row[3])
    brief.update({"brief_id": row[0], "generated_at": row[2],
                  "event_count": row[4], "degraded_flag": row[5]})
    return brief


@router.get("/api/brief")
async def get_brief(date: Optional[str] = Query(None),
                    days: int = Query(1, ge=1, le=30),
                    regenerate: bool = False):
    """取简报：默认最新一份；指定 date 取当天；regenerate=true 现场重算。

    现场生成含同步 LLM 调用，必须在 worker 线程跑（阻塞事件循环事故见模块 docstring）。
    """
    if not regenerate:
        conn = get_db()
        try:
            brief = _latest_brief(conn, date)
            if brief:
                return JSONResponse(brief)
        finally:
            conn.close()
    target_date = date or now_cn_iso()[:10]
    brief = await anyio.to_thread.run_sync(
        lambda: generate_brief(days=days, date=target_date, save=True))
    return JSONResponse(brief)


@router.get("/api/brief/dates")
async def brief_dates():
    """有简报的日期列表（页面日期切换用）。"""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT date, event_count, degraded FROM briefs ORDER BY date DESC LIMIT 60"
        ).fetchall()
    finally:
        conn.close()
    return JSONResponse({"dates": [
        {"date": r[0], "event_count": r[1], "degraded": bool(r[2])} for r in rows]})


@router.get("/api/brief/event/{message_id}/context")
async def event_context(message_id: int, around: int = Query(5, ge=0, le=20)):
    """事件溯源：原消息 + 同群前后各 N 条。"""
    conn = get_db()
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, sent_at, conversation_name, sender_name, text "
            "FROM messages WHERE id=?", (message_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="消息不存在")
        center = dict(row)
        rows = conn.execute(
            "SELECT id, sent_at, conversation_name, sender_name, text FROM messages "
            "WHERE conversation_name=? AND (sent_at < ? OR (sent_at = ? AND id <= ?)) "
            "ORDER BY sent_at DESC, id DESC LIMIT ?",
            (center["conversation_name"], center["sent_at"], center["sent_at"],
             center["id"], around + 1)).fetchall()
        before = [dict(r) for r in reversed(rows[1:])]
        rows2 = conn.execute(
            "SELECT id, sent_at, conversation_name, sender_name, text FROM messages "
            "WHERE conversation_name=? AND (sent_at > ? OR (sent_at = ? AND id > ?)) "
            "ORDER BY sent_at ASC, id ASC LIMIT ?",
            (center["conversation_name"], center["sent_at"], center["sent_at"],
             center["id"], around)).fetchall()
        after = [dict(r) for r in rows2]
        return JSONResponse({"center": center, "before": before, "after": after})
    finally:
        conn.close()


@router.get("/brief", response_class=HTMLResponse)
async def brief_page(date: Optional[str] = None):
    """简报页面：默认最新一份简报。生成走 worker 线程（同 /api/brief）。"""
    conn = get_db()
    try:
        brief = _latest_brief(conn, date)
        dates = [r[0] for r in conn.execute(
            "SELECT date FROM briefs ORDER BY date DESC LIMIT 60").fetchall()]
    finally:
        conn.close()
    if not brief:
        brief = await anyio.to_thread.run_sync(
            lambda: generate_brief(days=1, save=True))
        dates = [brief["date"]]
    template = _templates.get_template("brief.html")
    return HTMLResponse(template.render(brief=brief, dates=dates))


@router.post("/api/brief/reset")
async def reset_baseline():
    """清零重开：以当前时刻为新基线，清空简报存档，只监测此后新消息。

    含生成步骤，同样必须走 worker 线程。
    """
    from messagehub.brief import reset_and_set_baseline

    def _do():
        ts = reset_and_set_baseline()
        return ts, generate_brief(save=True)

    ts, brief = await anyio.to_thread.run_sync(_do)
    return JSONResponse({"baseline": ts, "brief": brief})
