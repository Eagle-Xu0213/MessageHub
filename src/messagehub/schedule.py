"""邮件整理调度器：唤醒补扫 + 加急插队 + 每小时汇总。

2026-11-20 重构后的三条发信路径（全部走 digest 引擎，共用一个整理水位）：
- 服务启动：立即「唤醒补扫」（kind=catchup），整理「上次关机 → 现在」的积压。
- 每 3 分钟：「加急检查」，直呼+指派+48h内截止的事件立即发邮件+桌面通知。
- 每 1 小时：「常规汇总」（kind=hourly），整理这一小时新增的普通工作事件。
空档静默、已发不重复；目标是用户无需打开微信翻群也不漏事、不被滥打扰。

旧的「每天 08:00 简报 + 整点滚动 + 推送钩子」机制已停用（定时点与你开机
时间无关、积压兜不住、增量邮件近似消息流）。generate_and_advance 等函数
保留仅为网页 /brief 按需生成简报使用，调度器不再自动调用。

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
    """后台调度线程（2026-11-20 重构）。

    旧行为（已停用）：每天 08:00 + 每小时整点生成简报并发邮件。
    问题：定时点和你开电脑时间无关，离开期间的积压靠固定钟点兜不住；
    8 点服务没起就直接漏掉，整点增量又和看消息流差不多。

    新行为（用户 2026-11-20 最终确认的完整机制）：
    - 线程启动时跑一次「唤醒补扫」（kind=catchup）——把上次整理水位到现在的
      积压事件用 LLM 打标签分组，发一封整理邮件（见 digest.run_catchup）。
    - 之后两个独立计时器并行：
      · 每 check_interval_sec（默认 3 分钟）跑「加急检查」——直呼 + 指派 +
        48h 内截止的事件立即发邮件 + 桌面通知（digest.run_urgent_check）。
      · 每 hourly_interval_sec（默认 1 小时）跑「常规汇总」（kind=hourly）——
        把这一小时新增的普通工作事件整理成邮件（digest.run_catchup）。
      两者共用同一个整理水位，互不重叠；空档静默，已发不重复。
    - 目标：用户平时无需打开微信翻群，急事插队、一般事定时送达，都不漏。
    - 不再自动调用 generate_and_advance；网页简报仍由 /brief 页面按需生成。
    """

    def __init__(self, morning_hour: int = 8, check_interval_sec: int = 180,
                 hourly_interval_sec: int = 3600):
        self.morning_hour = morning_hour  # 保留参数兼容旧调用/测试
        self.check_interval_sec = check_interval_sec
        self.hourly_interval_sec = hourly_interval_sec
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.state: Dict[str, Any] = {"running": False, "last_run": None,
                                      "last_reason": None}

    def start(self) -> Dict[str, Any]:
        if self._thread and self._thread.is_alive():
            return {"started": False, "reason": "already running"}
        self._stop.clear()
        self.state["running"] = True
        self._thread = threading.Thread(target=self._run, name="digest-scheduler",
                                        daemon=True)
        self._thread.start()
        return {"started": True, "check_interval_sec": self.check_interval_sec,
                "hourly_interval_sec": self.hourly_interval_sec}

    def stop(self) -> Dict[str, Any]:
        self._stop.set()
        self.state["running"] = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        return {"stopped": True}

    def _run(self):
        from messagehub import digest

        # 启动即唤醒补扫：整理离开期间的积压（LLM 调用在线程内，不阻塞服务启动）
        try:
            result = digest.run_catchup(kind="catchup")
            self.state["last_run"] = now_cn_iso()
            self.state["last_reason"] = "wakeup_catchup"
            print(f"[digest] 唤醒补扫: {result.get('event_count',0)} 事件, "
                  f"发出={result.get('sent')}")
        except Exception as e:
            print(f"[digest] 唤醒补扫失败: {type(e).__name__}: {e}")

        # 两个独立倒计时：谁先到就先触发谁，触发后各自重置，互不干扰。
        # 单线程顺序执行，不存在并发写水位的问题。
        urgent_countdown = float(self.check_interval_sec)
        hourly_countdown = float(self.hourly_interval_sec)
        while not self._stop.is_set():
            wait = min(urgent_countdown, hourly_countdown)
            if self._stop.wait(timeout=wait):
                break
            urgent_countdown -= wait
            hourly_countdown -= wait
            # 每小时常规汇总（先于加急判断，顺序无关，共用水位）
            if hourly_countdown <= 0:
                hourly_countdown = float(self.hourly_interval_sec)
                try:
                    result = digest.run_catchup(kind="hourly")
                    if result.get("sent"):
                        self.state["last_run"] = now_cn_iso()
                        self.state["last_reason"] = "hourly_digest"
                        print(f"[digest] 每小时汇总: {result.get('event_count',0)} 事件, "
                              f"发出={result.get('sent')}")
                except Exception as e:
                    print(f"[digest] 每小时汇总失败: {type(e).__name__}: {e}")
            # 加急检查
            if urgent_countdown <= 0:
                urgent_countdown = float(self.check_interval_sec)
                try:
                    result = digest.run_urgent_check()
                    if result.get("urgent_count"):
                        self.state["last_run"] = now_cn_iso()
                        self.state["last_reason"] = "urgent_check"
                        print(f"[digest] 加急检查: {result['urgent_count']} 条, "
                              f"邮件={result['sent']} 桌面={result['desktop']}")
                except Exception as e:
                    print(f"[digest] 加急检查失败: {type(e).__name__}: {e}")


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
