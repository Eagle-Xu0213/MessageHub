"""实时消息通道路由：/live 页面、采集状态/配置、SSE 流、ingest/inject 接入点。"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel
from typing import List, Optional

from messagehub.config import CN_TZ, TEMPLATES_DIR
from messagehub.pipeline.live import get_collector
from messagehub.adapters.uia_source import UIASource

router = APIRouter()

templates_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))


class LiveConfigRequest(BaseModel):
    interval: Optional[float] = None
    sources: Optional[List[str]] = None
    allowed_chats: Optional[List[str]] = None


@router.get("/live", response_class=HTMLResponse)
async def live_page():
    """实时消息接入页面"""
    return HTMLResponse(content=templates_env.get_template("live.html").render())


@router.get("/api/live/status")
async def live_status():
    """采集器状态 + 各数据源状态 + 最近消息流"""
    c = get_collector()
    s = c.status()
    s["recent"] = c.recent(60)
    return JSONResponse(s)


@router.post("/api/live/configure")
async def live_configure(req: LiveConfigRequest):
    """配置轮询间隔、启用的数据源、工作群白名单。"""
    cfg = get_collector().configure(
        interval=req.interval, sources=req.sources, allowed_chats=req.allowed_chats
    )
    return JSONResponse({"success": True, "config": cfg})


@router.post("/api/live/start")
async def live_start():
    return JSONResponse(get_collector().start())


@router.post("/api/live/stop")
async def live_stop():
    return JSONResponse(get_collector().stop())


@router.post("/api/live/poll")
async def live_poll():
    """手动触发一轮采集（用于验证与调试）"""
    return JSONResponse(get_collector().poll_once())


@router.get("/api/live/events")
async def live_events(since: int = 0, limit: int = 50):
    """按序号增量拉取新消息事件"""
    return JSONResponse({"events": get_collector().events_since(since, limit)})


@router.get("/api/live/stream")
async def live_stream(request: Request, since: int = 0):
    """SSE 实时消息流（页面订阅用）"""
    async def gen():
        cursor = since
        idle = 0
        while True:
            if await request.is_disconnected():
                break
            events = get_collector().events_since(cursor)
            for e in events:
                cursor = e["seq"]
                yield f"data: {json.dumps(e, ensure_ascii=False)}\n\n"
            idle += 1
            if idle >= 40:      # 每 20 秒发一次心跳，防止连接被中间层断开
                idle = 0
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.5)
    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/live/probe")
async def live_probe():
    """只读探测微信/QQ 窗口可读性 + 结构诊断，不返回任何聊天正文。

    UIA 正文采集已判定不可行（微信 4.2 自绘层），此接口仅保留只读结构诊断用途。
    """
    c = get_collector()
    src = UIASource(allowed_chats=c.cfg.get("allowed_chats") or [])
    p = src.probe()
    structures = []
    try:
        for info in src._find_chat_windows():
            structures.append({
                "platform": info["platform"], "window": info["chat_name"],
                **src._structure_report(info["window"]),
            })
    except Exception as e:
        structures.append({"error": f"{type(e).__name__}: {e}"})
    p["structure"] = structures
    return JSONResponse(p)


@router.post("/api/live/inject")
async def live_inject(payload: dict):
    """写入一条标准 JSONL 到实时目录（供外部导出工具/测试调用）。

    与 /api/live/ingest 的区别：本接口只是把消息落盘成文件，等 tail 轮询捡取；
    ingest 是直接 POST 入库。新接入方一律用 ingest。
    """
    live_dir = get_collector().live_dir
    live_dir.mkdir(parents=True, exist_ok=True)
    msg = {
        "source_platform": payload.get("source_platform", "wechat"),
        "conversation_id": payload.get("conversation_id", "live_stream"),
        "conversation_name": payload.get("conversation_name", "实时流入"),
        "sender_name": payload.get("sender_name", "unknown"),
        "sent_at": payload.get("sent_at")
                   or datetime.now(CN_TZ).isoformat(timespec="seconds"),
        "text": payload.get("text", ""),
        "source_message_id": payload.get("source_message_id"),
        "time_confidence": "exact" if payload.get("sent_at") else "capture_time",
    }
    if not str(msg["text"]).strip():
        raise HTTPException(status_code=400, detail="text 不能为空")
    with (live_dir / "live_stream.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    get_collector().poll_once()
    return JSONResponse({"success": True, "written": msg})


@router.post("/api/live/ingest")
async def live_ingest(request: Request):
    """标准实时接入点：外部采集工具把新消息 POST 进来，立即入库并推送到 SSE 流。

    这是第一阶段真正稳定的通道，不依赖微信/QQ 客户端的控件结构。
    支持三种请求体：
      {...}                        单条消息
      [{...}, {...}]               批量消息
      {"messages": [{...}, ...]}   批量包装

    最小字段 text；建议同时提供 conversation_name / sender_name / sent_at /
    source_platform / conversation_id / source_message_id。
    系统按 fingerprint 去重，重复推送不会重复入库。
    """
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="请求体必须是合法 JSON")
    if isinstance(data, dict) and isinstance(data.get("messages"), list):
        objs = data["messages"]
    elif isinstance(data, (dict, list)):
        objs = data
    else:
        raise HTTPException(status_code=400,
                            detail="请求体必须是对象、数组，或含 messages 数组的对象")
    result = get_collector().ingest(objs)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return JSONResponse({"success": True, **result})
