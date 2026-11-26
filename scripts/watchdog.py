"""Restore the local API and collector from the Windows background launcher."""
from datetime import datetime
import errno
from pathlib import Path
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "runtime" / "watchdog.log"


def log(message):
    with LOG.open("a", encoding="utf-8") as stream:
        stream.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")


def listening(port):
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def collector_running():
    # The collector holds a listen socket as a lock and never accepts connections.
    # Probe with an exclusive bind so periodic checks do not fill its backlog.
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(("127.0.0.1", 8765))
        except OSError as exc:
            if exc.errno in (errno.WSAEADDRINUSE, errno.WSAEACCES):
                return True
            raise
    return False


def start(*arguments):
    try:
        subprocess.Popen([sys.executable, *arguments], cwd=ROOT / "src",
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except OSError as exc:
        log(f"Could not start background Python: {exc}")
        return False
    return True


def main():
    LOG.parent.mkdir(parents=True, exist_ok=True)
    ok = True
    if not listening(8000):
        log("API down - starting uvicorn")
        ok = start("-m", "uvicorn", "messagehub.app:app", "--host", "127.0.0.1",
                   "--port", "8000", "--log-level", "warning", "--no-use-colors")
        if ok:
            time.sleep(2)
    if not collector_running():
        log("Collector down - starting moect_live loop")
        ok = start(str(ROOT / "scripts" / "moect_live.py"), "--loop", "--interval",
                   "120", "--no-refresh", "--selected-groups", "--include-private") and ok
    if LOG.exists() and LOG.stat().st_size > 200 * 1024:
        lines = LOG.read_text(encoding="utf-8-sig").splitlines(keepends=True)
        LOG.write_text("".join(lines[-200:]), encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
