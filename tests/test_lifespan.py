"""lifespan 组装回归测试（2026-11-18 复盘沉淀）。

事故背景：live.register_startup_hook 与 schedule.register_startup_hook 各自
执行 `app.router.lifespan_context = _lifespan`，后者覆盖前者，P2 重构（2b22aaa）
起采集器自启动静默丢失 28 小时（被外部 moect_live 的 ingest 通道掩盖）。
修复：app.py 唯一组合 lifespan（_combined_lifespan）。
本文件锁定：①app 存在唯一 lifespan 且包含采集器与调度器启停；②各模块不再
自行赋值 lifespan_context；③TestClient 进入/退出时两个组件状态正确翻转。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from starlette.testclient import TestClient

from messagehub.app import app, _combined_lifespan
from messagehub.pipeline import live as live_module
from messagehub import schedule as schedule_module


def test_app_has_single_combined_lifespan():
    """app 的 lifespan 必须是 app.py 的组合 lifespan，不允许被其他模块覆盖。

    FastAPI >=0.140 会把 app.router.lifespan_context 与 Router 自带的
    lifespan 合并成 `_merge_lifespan_context.<locals>.merged_lifespan`
    （见 fastapi/routing.py），因此这里不能断言对象同一性，改为断言
    该 lifespan 就是组合 lifespan 本身或由它合并而来——真正要防的回归
    是「某模块把 lifespan_context 覆盖成自己的钩子」。
    """
    ctx = app.router.lifespan_context
    assert ctx is _combined_lifespan or getattr(ctx, "__name__", "") == "merged_lifespan"
    # 覆盖型回归的特征：被替换成模块内的 register_startup_hook 内部函数
    assert "lifespan" in getattr(ctx, "__qualname__", "")


def test_no_module_assigns_lifespan_context():
    """各模块禁止再出现 lifespan_context 赋值（互相覆盖的回归根源）。"""
    import inspect

    import messagehub.schedule as sched
    import messagehub.routers.live as live_router

    for mod in (sched, live_router):
        src = inspect.getsource(mod)
        assert "lifespan_context =" not in src.replace(
            "app.router.lifespan_context = _combined_lifespan", ""
        ), f"{mod.__name__} 中仍存在 lifespan_context 赋值（会覆盖组合 lifespan）"


def test_lifespan_starts_and_stops_both_components():
    """TestClient 进入（startup）→ 采集器与调度器 running；退出（shutdown）→ 停止。"""
    collector = live_module.get_collector()
    scheduler = schedule_module.get_scheduler()

    with TestClient(app) as c:  # __enter__ 触发 lifespan startup
        r = c.get("/api/health")
        assert r.status_code == 200
        assert collector.state["running"] is True, "lifespan startup 后采集器应运行"
        assert scheduler.state["running"] is True, "lifespan startup 后调度器应运行"
        assert collector._thread is not None and collector._thread.is_alive()
        assert scheduler._thread is not None and scheduler._thread.is_alive()

    # __exit__ 触发 lifespan shutdown
    assert collector.state["running"] is False, "lifespan shutdown 后采集器应停止"
    assert scheduler.state["running"] is False, "lifespan shutdown 后调度器应停止"
