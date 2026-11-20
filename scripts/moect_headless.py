"""Headless Moect snapshot bridge (no GUI, no WebView2, no .NET).

Imports Moect's GPL source Service class directly and calls connect():
extract keys from running WeChat processes + decrypt + write snapshot.
This bypasses the flaky WebView2/pythonnet startup path entirely.

The Moect source tree is auto-detected (or set MOECT_SRC in .env); it is a
separate GPL clone, never bundled with this project.

Usage: python moect_headless.py [--once | --loop]
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

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


def resolve_moect_src() -> Path:
    """Locate the Moect GPL source tree. Env MOECT_SRC wins.

    Search order: MOECT_SRC → <project>/moect_src → ~/moect_src →
    %USERPROFILE%\\moect_src → D:/Projects/moect_src (legacy).
    """
    candidates: list[Path] = []
    env_src = (os.environ.get("MOECT_SRC") or "").strip()
    if env_src:
        candidates.append(Path(env_src).expanduser())
    candidates += [
        ROOT / "moect_src",
        Path.home() / "moect_src",
        Path(os.environ.get("USERPROFILE") or "") / "moect_src",
        Path("D:/Projects/moect_src"),
    ]
    for candidate in candidates:
        try:
            if candidate.is_dir() and (candidate / "core" / "service.py").is_file():
                return candidate.resolve()
        except OSError:
            continue
    raise RuntimeError(
        "找不到 Moect GPL 源码（需有 core/service.py）。"
        "请 clone https://github.com/mofan6/Moect 到本地，"
        "然后在项目 .env 里设置 MOECT_SRC=<源码目录的绝对路径>。"
    )


try:
    MOECT_SRC = resolve_moect_src()
    sys.path.insert(0, str(MOECT_SRC))
    log(f"Moect 源码目录: {MOECT_SRC}")
except RuntimeError as exc:
    log(f"启动失败: {exc}")
    raise


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


def pick_account(svc) -> str | None:
    """Detect running WeChat and bootstrap library config on a fresh install.

    Moect's startup_reconnect() is gated on a config written by its GUI
    (library.account_dir + tutorials_seen). A fresh clone has none of that,
    so we detect the account ourselves and call connect() directly. Key
    extraction happens inside connect() from the WeChat process memory —
    no GUI and no pre-existing keys.json needed.
    """
    detected = svc.detect()
    if not detected.get("running"):
        log("未检测到运行中的微信进程，请先登录微信桌面版")
        return None
    accounts = detected.get("accounts") or []
    if not accounts:
        log("detect 未找到微信账号数据目录")
        return None

    lib = svc.config.get("library") or {}
    wanted = lib.get("account_dir")
    chosen = accounts[0]
    if wanted:
        norm = lambda s: os.path.normcase(os.path.abspath(str(s or "")))
        for item in accounts:
            acct = svc.accounts.get(item["id"]) or {}
            if norm(acct.get("path")) == norm(wanted):
                chosen = item
                break

    account_id = chosen["id"]
    account = svc.accounts.get(account_id) or {}
    log(f"目标微信账号: {chosen.get('wxid') or account.get('wxid') or account_id}")
    if not lib.get("account_dir"):
        svc.config["library"] = {
            "root": str(current_snapshot()),
            "account_dir": account.get("path", ""),
            "name": account.get("dir_name", "微信账号"),
            "wxid": account.get("wxid", ""),
        }
        log("首次运行：已根据检测到的微信账号初始化 library 配置")
    return account_id


def run_connect() -> bool:
    """Trigger Service.connect() headlessly; report success."""
    from core.service import Service

    svc = Service(str(MOECT_SRC / "ui-qce"), root=str(DATA_DIR))
    try:
        before = snapshot_stamp(current_snapshot())
        account_id = pick_account(svc)
        if not account_id:
            return False
        result = svc.connect(account_id)
        log(f"connect 返回: {str(result)[:200]}")
        job = (result or {}).get("job") if isinstance(result, dict) else None
        if not job:
            log(f"connect 未产生任务: {result}")
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
    except Exception as e:
        log(f"connect 异常: {type(e).__name__}: {e}")
        return False
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
