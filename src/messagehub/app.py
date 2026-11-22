"""MessageHub FastAPI 入口：只负责组装路由与生命周期，业务逻辑在各 router。

启动：uvicorn messagehub.app:app（见 启动 MessageHub.bat / README §1）
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader
from contextlib import asynccontextmanager

from messagehub.config import CN_TZ, DB_PATH, STATIC_DIR, TEMPLATES_DIR, WATCH_DIR
from messagehub.database import get_db, init_db
from messagehub.pipeline.watcher import scan_directory, get_latest_capture_status
from messagehub.pipeline.importer import make_fingerprint, insert_messages
from messagehub.pipeline.live import get_collector
from messagehub.routers import brief, live, tasks, scope
from messagehub import schedule
from messagehub import mail as brief_mail
from messagehub import update
from messagehub import task_pipeline, task_runtime

app = FastAPI(title="MessageHub", version=update.__version__)


@asynccontextmanager
async def _combined_lifespan(_app: FastAPI):
    """唯一的 lifespan：同时启停实时采集器与简报调度器。

    背景（2026-11-18 复盘，回归修复）：原 live.register_startup_hook 与
    schedule.register_startup_hook 各自执行 `app.router.lifespan_context = ...`，
    后注册的 schedule 覆盖了 live——P2 重构（2b22aaa）起采集器自启动静默丢失。
    规则来源 RULES.md #5 单一事实来源：lifespan 只在这一处组装。
    """
    # ---- startup ----
    try:
        get_collector().start()
        print("[live] 实时采集已自动启动")
    except Exception as e:
        print(f"[live] 自动启动失败：{e}")
    try:
        task_runtime.get_scheduler().start()
        print("[brief-scheduler] 调度器已自动启动")
    except Exception as e:
        print(f"[brief-scheduler] 自动启动失败：{e}")
    yield
    # ---- shutdown ----
    try:
        get_collector().stop()
        print("[live] 实时采集已停止")
    except Exception as e:
        print(f"[live] 停止采集失败：{e}")
    try:
        task_runtime.get_scheduler().stop()
        print("[brief-scheduler] 调度器已停止")
    except Exception as e:
        print(f"[brief-scheduler] 停止调度器失败：{e}")


app.router.lifespan_context = _combined_lifespan

# 模板和静态文件
templates_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# 路由组装
app.include_router(tasks.router)
app.include_router(live.router)
app.include_router(brief.router)
app.include_router(scope.router)
# lifespan 统一在上方 _combined_lifespan 组装（采集器 + 调度器），
# 勿再对各模块的 register_startup_hook 赋值 lifespan_context（会互相覆盖，2026-11-18 回归教训）
brief_mail.register()  # 邮件桥：简报有实质内容时自动发到配置的收件箱

init_db()
task_pipeline.initialize_database(DB_PATH)


# ==================== 首页 / 健康 / 导入 / 扫描 ====================

@app.get("/", response_class=HTMLResponse)
async def index(request: None = None):
    """首页工作台"""
    conn = get_db()
    cursor = conn.cursor()

    # 统计信息
    cursor.execute("SELECT COUNT(*) FROM messages")
    total_messages = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM personal_tasks WHERE status IN ('pending', 'accepted', 'in_progress')")
    pending_tasks = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM personal_tasks WHERE status = 'completed'")
    completed_tasks = cursor.fetchone()[0]

    # 今日摘要：今天导入的消息数
    cursor.execute("SELECT COUNT(*) FROM messages WHERE sent_at >= ?",
                   (datetime.now(CN_TZ).strftime("%Y-%m-%d"),))
    today_messages = cursor.fetchone()[0]

    # 今日各群消息数（首页群列表面板：真实计数，按量排序）
    cursor.execute(
        "SELECT conversation_name, COUNT(*) AS cnt FROM messages "
        "WHERE sent_at >= ? AND conversation_name != '' "
        "GROUP BY conversation_name ORDER BY cnt DESC LIMIT 12",
        (datetime.now(CN_TZ).strftime("%Y-%m-%d"),))
    today_by_group = cursor.fetchall()

    # 采集状态
    cursor.execute("SELECT status, COUNT(*) FROM capture_runs GROUP BY status")
    capture_stats = {row[0]: row[1] for row in cursor.fetchall()}

    # 采集异常群数（error/partial 的轮次）
    cursor.execute("SELECT COUNT(*) FROM capture_runs WHERE status IN ('error','partial')")
    capture_errors = cursor.fetchone()[0]

    cursor.execute("SELECT DISTINCT conversation_name FROM messages")
    conversations = [row['conversation_name'] for row in cursor.fetchall()]

    conn.close()

    template = templates_env.get_template("index.html")

    return HTMLResponse(
        content=template.render(
            total_messages=total_messages,
            pending_tasks=pending_tasks,
            completed_tasks=completed_tasks,
            today_messages=today_messages,
            capture_stats=capture_stats,
            capture_errors=capture_errors,
            conversations=conversations[:10],  # 最近 10 个群
            today_by_group=today_by_group,
            app_version=update.__version__,
        )
    )


@app.get("/api/health")
@app.get("/health")
async def health_check():
    """健康检查（两个路径都支持，避免外部探测脚本误判）"""
    return {"status": "ok", "version": update.__version__}


@app.get("/api/version")
async def version_check(refresh: bool = False):
    """本地版本 + 上游最新版本比对。

    refresh=1 强制绕过缓存重查 GitHub；默认 1 小时缓存。
    """
    import anyio
    return await anyio.to_thread.run_sync(lambda: update.get_update_status(force=refresh))


@app.get('/api/update/status')
async def update_status():
    return update.apply_status()


@app.post('/api/update')
async def apply_update(request: Request):
    from urllib.parse import urlsplit
    origin = urlsplit(request.headers.get('origin', ''))
    if origin.scheme != 'http' or origin.hostname not in ('127.0.0.1', 'localhost') or origin.port != 8000:
        raise HTTPException(status_code=403, detail='只能从本机工作台启动更新')
    if request.headers.get('sec-fetch-site') not in (None, 'same-origin'):
        raise HTTPException(status_code=403, detail='只能从本机工作台启动更新')
    if update.sys.platform != 'win32':
        raise HTTPException(status_code=400, detail='一键更新目前仅支持 Windows')
    try:
        update.start_update()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {'state': 'starting'}


@app.post("/api/import")
async def import_messages(file: UploadFile = File(...)):
    """导入 JSONL/JSON/CSV/TXT 消息文件。

    解析与去重统一走 pipeline.importer（指纹算法全项目只有 make_fingerprint
    一套，修复了原实现与主链路指纹不一致导致的跨通道重复入库）。
    """
    if not file.filename.endswith(('.jsonl', '.json', '.csv', '.txt')):
        raise HTTPException(status_code=400, detail="不支持的文件格式")

    try:
        content = await file.read()

        if file.filename.endswith('.jsonl'):
            lines = content.decode('utf-8').strip().split('\n')
            messages = []
            for line in lines:
                if line.strip():
                    try:
                        obj = json.loads(line)
                        if isinstance(obj, dict):
                            obj.setdefault("source_platform", "manual")
                            obj.setdefault("source_method", "manual_import")
                            obj.setdefault("conversation_id", "unknown")
                            obj.setdefault("conversation_name", "manual_import")
                            obj.setdefault("sender_name", "unknown")
                            obj.setdefault("sent_at",
                                           datetime.now(CN_TZ).isoformat())
                            obj.setdefault("message_type", "text")
                            messages.append(obj)
                    except json.JSONDecodeError:
                        continue

        elif file.filename.endswith('.json'):
            data = json.loads(content.decode('utf-8'))
            if isinstance(data, list):
                messages = data
            else:
                messages = [data]

        else:
            # TXT/CSV：按行处理，每行一条消息文本
            messages = []
            for line in content.decode('utf-8').strip().split('\n'):
                messages.append({
                    "source_platform": "manual",
                    "source_method": "manual_import",
                    "conversation_id": file.filename,
                    "conversation_name": "手动导入",
                    "sender_name": "unknown",
                    "sent_at": datetime.now(CN_TZ).isoformat(),
                    "text": line,
                })

        if not messages:
            raise HTTPException(status_code=400, detail="文件中无有效消息")

        # 统一入库（make_fingerprint + 事务批量插入）
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        try:
            stats = insert_messages(conn, messages)
        finally:
            conn.close()

        return JSONResponse({
            "status": "ok",
            "inserted": stats["inserted"],
            "duplicate": stats["duplicate"],
            "total": stats["total"],
        })

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导入失败：{str(e)}")


@app.post("/api/scan")
async def trigger_scan():
    """手动触发一次目录扫描（自动导入 exports/imports 下新增文件）+ 自动提取任务。"""
    if not WATCH_DIR.exists():
        WATCH_DIR.mkdir(parents=True, exist_ok=True)
    result = scan_directory(str(DB_PATH), str(WATCH_DIR))
    # 扫描后自动运行规则提取（LLM 仅在配置后启用）。
    # 线程池执行：run_extraction 全表扫描 + 每条可能同步调 LLM（15s/条），
    # 在 async 里跑会卡死事件循环（2026-11-19 事故，规则同 brief.py）
    import anyio
    def process_new():
        import sqlite3
        with sqlite3.connect(str(DB_PATH), timeout=30) as conn:
            created = 0
            while True:
                batch = task_pipeline.process_batch(conn)
                created += batch["created"]
                if batch["processed"] < 200:
                    return {"created": created, "merged": 0}
    ext = await anyio.to_thread.run_sync(process_new)
    return JSONResponse({
        "status": result["status"],
        "new": result["new"],
        "duplicate": result["duplicate"],
        "files_seen": result["files_seen"],
        "tasks_created": ext["created"],
        "tasks_merged": ext["merged"],
        "errors": result["errors"],
    })


@app.get("/api/capture-status")
async def capture_status():
    """采集运行状态（区分 成功无新/失败/不完整/未开始）。"""
    runs = get_latest_capture_status(str(DB_PATH))
    # 统计消息覆盖
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM messages")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT conversation_name) FROM messages")
    convs = cur.fetchone()[0]
    conn.close()
    return JSONResponse({
        "runs": runs,
        "total_messages": total,
        "conversation_count": convs,
        "watch_dir": str(WATCH_DIR),
    })


# 测试兼容 re-export（tests/test_p0.py 从这里导入；指纹函数已统一到 importer）
from messagehub.pipeline.importer import make_fingerprint as MessageAdapter  # noqa: E402,F401
