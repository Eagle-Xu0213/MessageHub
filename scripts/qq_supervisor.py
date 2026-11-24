"""Run the local NapCat instance under the Windows logon task."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import msvcrt
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
STATE = RUNTIME / "qq_supervisor.json"
STOP = RUNTIME / "qq_supervisor.stop"
POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"


def stamp():
    return datetime.now().astimezone().isoformat()


_state = None


def state_update(**changes):
    global _state
    if _state is None:
        try:
            _state = json.loads(STATE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _state = {}
    _state.update(changes)
    temp = STATE.with_suffix(".tmp")
    try:
        temp.write_text(json.dumps(_state), encoding="utf-8")
        temp.replace(STATE)
    except OSError:
        pass


def external_instance(node):
    escaped = str(node).replace("'", "''")
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command",
         f"if (Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" -ErrorAction Stop | "
         f"Where-Object ExecutablePath -eq '{escaped}') {{exit 10}}"],
        capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode not in (0, 10):
        raise OSError("无法核对 NapCat 进程归属。")
    return result.returncode == 10


def read_phases(stream, phases):
    # Raw console output can contain login material. Retain only fixed categories.
    with stream:
        for raw in stream:
            line = raw.decode("utf-8", errors="replace")
            if "GetQQLoginQrcode" in line or "\u4e8c\u7ef4\u7801\u5df2\u4fdd\u5b58" in line:
                phases["phase"] = "waiting_login"
            elif "\u5df2\u901a\u77e5\u4e3b\u8fdb\u7a0b\u767b\u5f55\u6210\u529f" in line:
                phases["phase"] = "waiting_handshake"
            elif "\u65e0\u6cd5\u91cd\u590d\u767b\u5f55" in line:
                phases["issue"] = "duplicate_login"


class BasicJobLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
        ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
        ("max_working_set", ctypes.c_size_t), ("process_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD),
    ]


class JobLimits(ctypes.Structure):
    _fields_ = [
        ("basic", BasicJobLimits), ("io_counters", ctypes.c_uint64 * 6),
        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t),
    ]


class NapCatJob:
    def __init__(self):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = JobLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def add(self, process):
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        self.api.CloseHandle(self.handle)


def supervise(root):
    node = root / "node.exe"
    if not node.is_file() or not (root / "index.js").is_file():
        state_update(phase="stopped", issue="installation_missing", updated_at=stamp())
        return
    STOP.unlink(missing_ok=True)
    delay = 5
    while not STOP.exists():
        job = None
        process = None
        try:
            if external_instance(node):
                state_update(phase="external_instance", updated_at=stamp(), pid=None)
                return
            phases = {"phase": "waiting_login", "issue": None}
            job = NapCatJob()
            process = subprocess.Popen([str(node), str(root / "index.js")], cwd=root,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
            job.add(process)
        except (OSError, subprocess.TimeoutExpired):
            if process is not None:
                process.kill()
                process.wait()
                process.stdout.close()
            if job is not None:
                job.close()
            state_update(phase="restarting", issue="launch_failed", pid=None, updated_at=stamp())
        else:
            started = time.monotonic()
            reader = threading.Thread(target=read_phases, args=(process.stdout, phases), daemon=True)
            reader.start()
            try:
                while process.poll() is None and not STOP.exists():
                    state_update(**phases.copy(), updated_at=stamp(), pid=process.pid)
                    time.sleep(2)
            finally:
                job.close()
                code = process.wait(timeout=10)
            reader.join(timeout=1)
            stopped = STOP.exists()
            state_update(phase="stopped" if stopped else "restarting", pid=None,
                         exit_code=code, exited_at=stamp(), issue=phases["issue"],
                         updated_at=stamp())
            if time.monotonic() - started >= 120:
                delay = 5
        until = time.monotonic() + delay
        while not STOP.exists() and time.monotonic() < until:
            time.sleep(1)
        delay = min(delay * 2, 60)
    STOP.unlink(missing_ok=True)
    state_update(phase="stopped", pid=None, updated_at=stamp())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("napcat", type=Path)
    root = parser.parse_args().napcat.resolve()
    RUNTIME.mkdir(exist_ok=True)
    with (RUNTIME / "qq_supervisor.lock").open("a+b") as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return
        supervise(root)


if __name__ == "__main__":
    main()
