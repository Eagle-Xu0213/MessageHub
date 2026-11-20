"""调度器三路径计时逻辑测试：启动补扫 / 加急 / 每小时汇总。

不真发信、不真调 LLM：monkeypatch digest 的三个入口为记录调用的假函数，
用极短间隔驱动线程，验证三条路径都被触发且 mistake-free。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from messagehub import digest, schedule


def _wait_until(predicate, timeout=3.0, tick=0.02):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(tick)
    return False


def test_scheduler_fires_all_three_paths(monkeypatch):
    """启动补扫一次 + 加急 + 每小时汇总都被触发，且 kind 参数正确。"""
    calls = {"catchup": [], "urgent": 0}

    def fake_catchup(send_mail=True, kind="catchup", **kwargs):
        calls["catchup"].append(kind)
        return {"event_count": 0, "sent": False, "skipped": "test"}

    def fake_urgent(send=True):
        calls["urgent"] += 1
        return {"urgent_count": 0}

    monkeypatch.setattr(digest, "run_catchup", fake_catchup)
    monkeypatch.setattr(digest, "run_urgent_check", fake_urgent)

    s = schedule.BriefScheduler(check_interval_sec=0.05,
                                hourly_interval_sec=0.08)
    s.start()
    ok = _wait_until(
        lambda: "hourly" in calls["catchup"] and calls["urgent"] >= 2,
        timeout=3.0)
    s.stop()

    assert ok, (
        f"三条路径未在时限内全部触发: {calls}")
    assert calls["catchup"][0] == "catchup", "启动第一次必须是唤醒补扫"
    assert "hourly" in calls["catchup"], "每小时汇总定时器必须触发"
    assert calls["urgent"] >= 2, "加急检查应按短周期反复触发"


def test_scheduler_stop_is_clean(monkeypatch):
    """stop 后线程终止、计数不再增长。"""
    counter = {"n": 0}

    def fake_catchup(*a, **k):
        counter["n"] += 1
        return {"sent": False}

    monkeypatch.setattr(digest, "run_catchup", fake_catchup)
    monkeypatch.setattr(digest, "run_urgent_check", lambda *a, **k: {"urgent_count": 0})

    s = schedule.BriefScheduler(check_interval_sec=0.05,
                                hourly_interval_sec=0.05)
    s.start()
    time.sleep(0.2)
    s.stop()
    assert not s._thread.is_alive(), "stop 后线程应终止"
    n_at_stop = counter["n"]
    time.sleep(0.1)
    assert counter["n"] == n_at_stop, "stop 后不应再触发汇总"
