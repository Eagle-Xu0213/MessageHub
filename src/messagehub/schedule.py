"""简报调度器（R4）：按节奏自动生成简报 + 推进基线 + 触发通知钩子。

节奏（用户确认的设计）：
- 每天 08:00 生成「今日待办」简报（事件窗口：上次简报之后的新消息），
  生成后把基线推进到生成时刻，实现滚动监测——昨天看过的事件不再重复出现。
- 每小时整点滚动检查：有新事件才重新生成并推进；无新事件保持沉默。
- 紧急事件（工作群 @我/指派 + 截止词）由采集链路即时推送钩子（R5 邮件桥接此）。

实现：独立 daemon 线程挂在 FastAPI lifespan 上，与采集器线程并存。
生成走同步 SQLite + 可选 LLM，全部在 worker 线程执行，不碰事件循环。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from messagehub.brief import (
    generate_brief, get_baseline, set_baseline, BASELINE_FILE,
)
from messagehub.config import CN_TZ, DB_PATH, now_cn_iso

# 通知钩子：R5 邮件桥 / Server酱推送注册到这里；签名 fn(brief_dict) -> None
_NOTIFY_HOOKS: List[Any] = []


def register_notify_hook(fn: Any) -> None:
    """注册简报通知钩子（幂等）。"""
    if fn not in _NOTIFY_HOOKS:
        _NOTIFY_HOOKS.append(fn)


def notify_if_content(brief: Dict[str, Any], reason: str) -> None:
    """有实质内容才通知——无事保持沉默（用户定的节奏）。"""
    stats = brief.get("stats", {})
    if any(stats.get(k) for k in ("todos", "replies", "notices")):
        _fire_hooks(brief, reason)


def _fire_hooks(brief: Dict[str, Any], reason: str) -> None:
    """触发所有通知钩子；单个钩子失败不影响其他。"""
    payload = dict(brief)
    payload["notify_reason"] = reason
    for fn in _NOTIFY_HOOKS:
        try:
            fn(payload)
        except Exception as e:
            print(f"[brief-scheduler] 通知钩子 {getattr(fn, '__name__', fn)} 失败: {e}")


def has_new_events_since_baseline() -> bool:
    """基线之后是否有新的工作事件候选（粗查：工作/监控群有无新消息）。"""
    base = get_baseline()
    if not base:
        return True
    conn = sqlite3.connect(str(DB_PATH))
    try:
        rows = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE sent_at > ? "
            "AND (conversation_name LIKE '%@chatroom' OR 1=1)",
            (base,)).fetchone()
        # 粗查：基线后同群有任何新消息就值得重新生成（规则过滤在生成时做）
        n = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE sent_at > ?", (base,)).fetchone()[0]
        return n > 0
    finally:
        conn.close()


def generate_and_advance(reason: str = "scheduled") -> Optional[Dict[str, Any]]:
    """生成简报并把基线推进到当前时刻。返回简报 dict。"""
    brief = generate_brief(save=True)
    ts = set_baseline()
    brief["baseline_advanced_to"] = ts
    # 有实质内容（任一栏非空）才触发通知钩子——无事保持沉默
    notify_if_content(brief, reason)
    return brief


def _seconds_until_next(hour: int, minute: int = 0) -> float:
    """距下一个 HH:MM（北京时间）的秒数。"""
    now = datetime.now(CN_TZ)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return (target - now).total_seconds()


class BriefScheduler:
    """简报定时线程：每天 08:00 生成 + 每小时滚动检查。"""

    def __init__(self, morning_hour: int = 8, check_interval_sec: int = 3600):
        self.morning_hour = morning_hour
        self.check_interval_sec = check_interval_sec
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.state: Dict[str, Any] = {"running": False, "last_run": None,
                                      "last_reason": None}

    def start(self) -> Dict[str, Any]:
        if self._thread and self._thread.is_alive():
            return {"started": False, "reason": "already running"}
        self._stop.clear()
        self.state["running"] = True
        self._thread = threading.Thread(target=self._run, name="brief-scheduler",
                                        daemon=True)
        self._thread.start()
        return {"started": True, "morning_hour": self.morning_hour}

    def stop(self) -> Dict[str, Any]:
        self._stop.set()
        self.state["running"] = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        return {"stopped": True}

    def _run(self):
        # 启动时若当天还没有简报且基线后有消息 → 立即补一份
        try:
            if has_new_events_since_baseline():
                generate_and_advance(reason="startup_catchup")
                self.state["last_run"] = now_cn_iso()
                self.state["last_reason"] = "startup_catchup"
        except Exception as e:
            print(f"[brief-scheduler] 启动补发失败: {e}")

        next_morning = _seconds_until_next(self.morning_hour)
        next_check = float(self.check_interval_sec)
        while not self._stop.is_set():
            wait = min(next_morning, next_check)
            if self._stop.wait(timeout=wait):
                break
            now = datetime.now(CN_TZ)
            fired = None
            if next_morning <= next_check:
                fired = "morning"
            else:
                fired = "hourly" if has_new_events_since_baseline() else None
            try:
                if fired:
                    generate_and_advance(reason=fired)
                    self.state["last_run"] = now_cn_iso()
                    self.state["last_reason"] = fired
            except Exception as e:
                print(f"[brief-scheduler] {fired} 生成失败: {e}")
            # 重置两个计时器（都从现在起算）
            next_morning = _seconds_until_next(self.morning_hour)
            next_check = float(self.check_interval_sec)


_scheduler: Optional[BriefScheduler] = None


def get_scheduler() -> BriefScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BriefScheduler()
    return _scheduler


def register_startup_hook(app) -> None:
    """[已废弃 2026-11-18] 原实现直接赋值 app.router.lifespan_context，
    会覆盖 routers/live.py 注册的采集器 lifespan（后注册者胜，采集器自启动
    静默丢失 28 小时）。统一 lifespan 现由 app.py 组装，见 app.py `_combined_lifespan`。
    保留空函数是为了不破坏潜在的外部调用；项目内调用点已删除。"""
    return None
