"""「去年今日」回顾路由：/memories 页面、/api/memories* 接口、AI 摘要缓存。"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel

from messagehub.config import CN_TZ, TEMPLATES_DIR, friendly_name
from messagehub.database import get_db

router = APIRouter()

templates_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))


@router.get("/memories", response_class=HTMLResponse)
async def memories_page():
    """去年今日回顾页面"""
    template = templates_env.get_template("memories.html")
    return HTMLResponse(content=template.render())


def _day_bounds(date_str: str) -> tuple[str, str]:
    """日期字符串 → 当天 [start_ts, end_ts)（北京时间 ISO）。"""
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    start = datetime.combine(d, datetime.min.time())
    end = datetime.combine(d, datetime.max.time())
    return (start.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            end.strftime("%Y-%m-%dT%H:%M:%S+08:00"))


@router.get("/api/memories")
async def get_memories(
    date: Optional[str] = None,
    conversation_id: Optional[str] = None,
    limit: int = 0
):
    """获取指定日期（默认去年今日）的聊天记录。

    limit=0 表示返回全部消息，避免回顾内容被截断。
    """
    conn = get_db()
    cursor = conn.cursor()

    # 默认使用"今天"作为基准，计算去年同日
    if not date:
        today = datetime.now(CN_TZ).date()
        target_date = today.replace(year=today.year - 1)
    else:
        try:
            target_date = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            return JSONResponse({"error": "Invalid date format", "messages": []})

    start_ts, end_ts = _day_bounds(target_date.isoformat())

    # 构建查询
    query = "SELECT * FROM messages WHERE sent_at >= ? AND sent_at < ?"
    params = [start_ts, end_ts]

    if conversation_id:
        query += " AND conversation_id = ?"
        params.append(conversation_id)

    query += " ORDER BY sent_at ASC"
    if limit:
        query += f" LIMIT {limit}"

    cursor.execute(query, params)
    messages = [dict(row) for row in cursor.fetchall()]

    # 群名友好化（同时修正返回值与统计）
    for m in messages:
        m["conversation_name"] = friendly_name(
            str(m.get("conversation_id") or ""),
            str(m.get("conversation_name") or ""),
        )

    # 统计信息
    cursor.execute(
        "SELECT COUNT(*), COUNT(DISTINCT conversation_id), COUNT(DISTINCT sender_name) "
        "FROM messages WHERE sent_at >= ? AND sent_at < ?",
        [start_ts, end_ts]
    )
    total_msgs, unique_convs, unique_senders = cursor.fetchone()

    # 按群分组统计
    cursor.execute(
        "SELECT conversation_id, conversation_name, COUNT(*) as msg_count, COUNT(DISTINCT sender_name) as member_count "
        "FROM messages WHERE sent_at >= ? AND sent_at < ? GROUP BY conversation_id",
        [start_ts, end_ts]
    )
    group_stats = [dict(row) for row in cursor.fetchall()]
    for g in group_stats:
        g["conversation_name"] = friendly_name(
            str(g.get("conversation_id") or ""),
            str(g.get("conversation_name") or ""),
        )

    conn.close()

    return JSONResponse({
        "target_date": target_date.isoformat(),
        "total_messages": total_msgs,
        "active_chats": unique_convs,
        "active_members": unique_senders,
        "group_stats": group_stats,
        "messages": messages,
        "has_data": total_msgs > 0,
    })


@router.get("/api/memories/groups")
async def list_groups():
    """获取所有可选择的群聊列表"""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute(
        "SELECT DISTINCT conversation_id, conversation_name FROM messages "
        "WHERE conversation_id IS NOT NULL AND conversation_id != '' "
        "AND conversation_id != 'unknown' AND sent_at LIKE '20%' "
        "ORDER BY conversation_name"
    )
    groups = []
    for row in cursor.fetchall():
        g = dict(row)
        g["conversation_name"] = friendly_name(
            str(g.get("conversation_id") or ""),
            str(g.get("conversation_name") or ""),
        )
        groups.append(g)

    conn.close()

    return JSONResponse({"groups": groups})


# ==================== AI 回顾总结 ====================

class MemorySummarizeRequest(BaseModel):
    date: str
    conversation_id: Optional[str] = None
    force_refresh: bool = False


@router.post("/api/memories/summarize")
async def summarize_memories(request: MemorySummarizeRequest):
    """为指定日期生成 AI 回顾摘要"""
    from messagehub.config import BASE_DIR
    from messagehub.llm.provider import LLMProvider

    conn = get_db()
    cursor = conn.cursor()

    # 查询该日期的消息
    start_ts, end_ts = _day_bounds(request.date)

    query = "SELECT * FROM messages WHERE sent_at >= ? AND sent_at < ?"
    params = [start_ts, end_ts]

    if request.conversation_id:
        query += " AND conversation_id = ?"
        params.append(request.conversation_id)

    cursor.execute(query, params)
    messages = [dict(row) for row in cursor.fetchall()]

    if not messages:
        conn.close()
        return JSONResponse({
            "success": False,
            "error": "该日期无消息记录",
            "summary": None,
        })

    # 获取群聊名称（友好化）
    conversation_name = friendly_name(
        str(messages[0].get("conversation_id") or ""),
        str(messages[0].get("conversation_name") or ""),
    )

    conn.close()

    # 优先读取已生成的摘要缓存（离线报告脚本会写），命中则秒回且不受网络限流影响
    cache_path = BASE_DIR / "reports" / f"ai_summary_{request.date}.json"
    if not request.force_refresh and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            return JSONResponse({
                "success": True,
                "error": None,
                "summary": cached,
                "fallback_only": bool(cached.get("degraded")),
                "cached": True,
            })
        except Exception:
            pass

    # 调用 LLM 生成摘要（provider 内部已含降级，永不返回 None）
    llm = LLMProvider.from_env()
    summary = llm.summarize_memories(messages, conversation_name, request.date)
    degraded = bool(summary.get("degraded")) if summary else True

    # 回写缓存，供下次与离线报告复用
    try:
        cache_path.parent.mkdir(exist_ok=True)
        cache_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    return JSONResponse({
        "success": True,
        "error": None,
        "summary": summary,
        "fallback_only": degraded,
        "cached": False,
        "note": "本地规则摘要（AI 暂不可用）" if degraded else None,
    })
