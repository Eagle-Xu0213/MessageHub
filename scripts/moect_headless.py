"""Headless Moect snapshot bridge (no GUI, no WebView2, no .NET).

Uses Moect's GPL source (D:/Projects/moect_src) Service class directly:
connect() = extract keys from running WeChat processes + decrypt + snapshot.
This bypasses the flaky WebView2/pythonnet startup path entirely.

Usage: python moect_headless.py [--once | --loop]
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, "D:/Projects/moect_src")

BJ = timezone(timedelta(hours=8))
LOG_FILE = ROOT / "runtime" / "moect_headless.log"
STATE_FILE = ROOT / "runtime" / "moect_live_state.json"
DATA_DIR = ROOT / "runtime" / "moect_data"
ARCHIVES = DATA_DIR / "archives"

try:
    from dotenv import load_dotenv
    load_dotenv(str(ROOT / ".env"), override=False)
except ImportError:
    pass


def log(msg: str) -> None:
    line = f"[{datetime.now(BJ):%H:%M:%S}] {msg}"
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def current_snapshot() -> Path:
    """The fixed merged directory consumed by moect_live.py."""
    return ARCHIVES / "current"


def snapshot_stamp(p: Path) -> tuple | None:
    """Stable signature of a complete snapshot (for change detection)."""
    try:
        m1 = p / "message" / "message_1.db"
        if not m1.is_file() or m1.stat().st_size < 1024 * 1024:
            return None
        sig = tuple(sorted(
            (f.name, f.stat().st_size)
            for f in p.rglob("*.db") if f.is_file()))
        return sig if sum(s for _, s in sig) > 800 * 1024 * 1024 else None
    except Exception:
        return None


def run_connect() -> bool:
    """Trigger Service.connect() headlessly; report success."""
    from core.service import Service

    svc = Service("D:/Projects/moect_src/ui-qce", root=str(DATA_DIR))
    try:
        lib = svc.config.get("library") or {}
        if not lib or not lib.get("account_dir"):
            log("config 无 library/account_dir，跳过")
            return False
        before = snapshot_stamp(current_snapshot())
        # connect(account_id)：detect 找到与 library.account_dir 匹配的账号
        result = svc.startup_reconnect()
        log(f"startup_reconnect 返回: {str(result)[:200]}")
        job = (result or {}).get("job") if isinstance(result, dict) else None
        if not job:
            log(f"startup_reconnect 未产生任务: {result}")
            return False
        job_id = job if isinstance(job, str) else job["id"]
        log(f"connect 任务已创建 {job_id}，等待完成…")
        deadline = time.time() + 600
        while time.time() < deadline:
            time.sleep(5)
            with svc.lock:
                task = next((t for t in svc.config["tasks"]
                             if t["id"] == job_id), None)
            st = task.get("status") if task else "?"
            prog = task.get("progress") if task else 0
            if st in ("completed", "failed", "interrupted", "cancelled"):
                log(f"connect 结束: {st} ({prog}%) detail={task.get('detail', '')}")
                break
        else:
            log("connect 超时 600s")
            return False
        if st != "completed":
            return False
        # 服务把快照写到 archives/<account_id>/snapshot_xxx，并更新 library.root
        new_root = svc.config["library"]["root"]
        log(f"新快照: {new_root}")
        # 等待快照写完整（最后一次 db 尺寸稳定）
        return bool(snapshot_stamp(Path(new_root)) or
                    snapshot_stamp(current_snapshot()))
    finally:
        try:
            svc.shutdown()
        except Exception:
            pass


def main() -> int:
    once = "--loop" not in sys.argv
    log(f"headless bridge 启动 mode={'once' if once else 'loop'}")
    ok = run_connect()
    log(f"本轮结果: {'OK' if ok else 'FAIL'}")
    if once:
        return 0 if ok else 1
    interval = 120
    while True:
        time.sleep(interval)
        try:
            run_connect()
        except Exception as e:
            log(f"本轮异常: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
