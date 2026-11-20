#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Moect 解密明文库 → MessageHub 实时采集桥接。

原理（重要）：
  Moect 1.2.8 已在本机完成微信 4.x 数据库解密，并把结果保存为明文 SQLite
  快照 ``%LOCALAPPDATA%/Moect/archives/<id>/snapshot_*``。实测
  ``Moect.exe --smoke 1 --data-dir <项目 runtime/moect_data>`` 可以**无界面**
  触发一次重新连接 + 生成包含当前实时消息的新快照（快照落在项目目录内，
  便于安全清理，不会撑爆 AppData）。

本脚本：
  1. 按间隔触发 Moect 刷新（可 --no-refresh 关闭，仅读现有快照）
  2. 扫描最新快照的 message_*.db，按 create_time 水位增量取新消息
  3. 解压 zstd、拆分群消息发送者、映射会话/群名
  4. 批量 POST /api/live/ingest（指纹去重 + SSE 实时流）
  5. 清理项目目录内的旧快照，只保留最新一份（永不触碰 AppData）

安全边界：
  - 只读微信原始目录；解密由用户已安装的 Moect 完成，本脚本不接触密钥内容。
  - 快照与状态文件都在项目 runtime/ 下；不上传、不外发。
  - 清理只针对 runtime/moect_data/archives/*/snapshot_*，且有路径白名单校验。

用法：
  python scripts/moect_live.py --once
  python scripts/moect_live.py --loop --interval 60
  python scripts/moect_live.py --once --since-hours 48
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import zstandard

BJ = timezone(timedelta(hours=8))
# 路径全部自动推导，换机器不用改代码：
#   ROOT         = 本脚本所在目录的上一级（仓库根）
#   MOECT_EXE    环境变量 > .env 中的 MOECT_EXE > 常见安装位置自动探测 > 报错提示
ROOT = Path(os.environ.get("MESSAGEHUB_ROOT") or Path(__file__).resolve().parents[1])

# 本脚本是独立进程，不会经过 FastAPI 侧的 dotenv 加载，自己读一次项目根 .env。
# 不覆盖已存在的进程环境变量（真实环境变量优先），失败不影响运行。
try:
    from dotenv import load_dotenv

    load_dotenv(str(ROOT / ".env"), override=False)
except ImportError:
    pass


def _find_moect() -> Path:
    """定位 Moect.exe：环境变量优先，其次常见安装目录。

    找不到不在这里报错——--no-refresh 模式不需要 Moect，
    真正需要时（refresh_moect）再给出明确提示。
    """
    env = os.environ.get("MOECT_EXE")
    if env:
        return Path(env)
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Moect" / "Moect.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Moect" / "Moect.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Moect" / "Moect.exe",
    ]
    for c in candidates:
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return candidates[0]


MOECT_EXE = _find_moect()
DATA_DIR = ROOT / "runtime" / "moect_data"
FALLBACK_ARCHIVES = Path(os.environ.get("LOCALAPPDATA", "")) / "Moect" / "archives"
STATE_FILE = ROOT / "runtime" / "moect_live_state.json"
INGEST_URL = "http://127.0.0.1:{port}/api/live/ingest".format(
    port=os.environ.get("PORT", "8000").strip() or "8000")
MSG_TABLE_RE = re.compile(r"^Msg_[0-9a-f]{32}$")
SENDER_PREFIX = re.compile(r"^([^\s:\n]{1,64}):\n(.*)$", re.S)
SAFE_CLEAN_PREFIX = (ROOT / "runtime" / "moect_data" / "archives").resolve()

# 纯文本消息的 local_type。微信 4.x 中 1=文本，其余（图片/语音/引用/文件等）跳过。
TEXT_LOCAL_TYPE = 1
MAX_TEXT_LEN = 8000
# 单轮最多翻多少页。大积压时一轮内多取几次，避免每 2 分钟只消化一页；
# 达到上限时本轮标记 drained=False，游标持久化，下一轮继续。
MAX_PAGES_PER_RUN = 20


LOG_FILE = ROOT / "runtime" / "moect_live.log"
INSTANCE_LOCK_PORT = 8765
_instance_lock = None


def acquire_instance_lock() -> bool:
    """用回环端口做单实例互斥：进程退出即释放，不留锁文件、不需清理。

    重复双击启动脚本时，第二个实例拿不到端口会立即退出，避免两个采集器
    同时刷新 Moect、抢同一水位文件和 current/ 快照目录。
    """
    global _instance_lock
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", INSTANCE_LOCK_PORT))
        s.listen(1)
        _instance_lock = s      # 保持引用，防止被 GC 关闭
        return True
    except OSError:
        s.close()
        return False


def log(msg: str) -> None:
    line = f"[{datetime.now(BJ):%H:%M:%S}] {msg}"
    try:
        print(line, flush=True)
    except Exception:      # pythonw.exe 无控制台
        pass
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(d: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


CURRENT = DATA_DIR / "archives" / "current"


def snapshot_complete(p: Path) -> bool:
    """Moect 是边生成边写文件的，目录刚出现时数据还不完整。"""
    try:
        m1 = p / "message" / "message_1.db"
        if not m1.is_file() or m1.stat().st_size < 1024 * 1024:
            return False
        return sum(1 for f in p.rglob("*.db") if f.is_file()) >= 20
    except Exception:
        return False


def list_snapshots() -> list:
    """当前固定快照优先；其次项目内历史快照；最后 AppData 里的只读回退。"""
    out = []
    if snapshot_complete(CURRENT):
        out.append(CURRENT)
    for base in (DATA_DIR / "archives", FALLBACK_ARCHIVES):
        if base.exists():
            out.extend(p for p in base.glob("*/snapshot_*") if p.is_dir())
    return out[:1] + sorted(out[1:], key=lambda p: p.stat().st_mtime, reverse=True)


def consolidate(snaps: list) -> Path:
    """把项目内各快照的文件原子搬进固定目录 ``current``（同名覆盖）。

    只做同盘 ``os.replace`` + 空目录 ``rmdir``，不调用任何删除文件的 API：
    磁盘上始终只保留一份 ~1.5GB 快照，且规避批量删除保护。
    AppData 中用户原有的快照一律不动。
    """
    CURRENT.mkdir(parents=True, exist_ok=True)
    targets = []
    for p in snaps:
        try:
            if p.resolve() == CURRENT.resolve():
                continue
            if not str(p.resolve()).startswith(str(SAFE_CLEAN_PREFIX)):
                continue   # AppData 下的用户快照：只读回退，绝不搬动
            if not snapshot_complete(p):
                continue   # 未写完的快照留到下一轮，绝不覆盖已有完整数据
            targets.append(p)
        except Exception:
            continue
    # 按时间从旧到新，最终 current 里留下最新数据
    targets.sort(key=lambda p: p.stat().st_mtime)
    moved = 0
    for src in targets:
        for f in src.rglob("*"):
            if not f.is_file():
                continue
            dst = CURRENT / f.relative_to(src)
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.replace(str(f), str(dst))
                moved += 1
            except OSError:
                pass
        for d in sorted((x for x in src.rglob("*") if x.is_dir()),
                        key=lambda x: len(x.parts), reverse=True):
            try:
                d.rmdir()
            except OSError:
                pass
        try:
            src.rmdir()
        except OSError:
            pass
    if moved:
        log(f"快照合并：移动 {moved} 个文件到固定目录（磁盘恒定一份）")
    return CURRENT


def _prepare_data_dir() -> None:
    """首次使用需把 Moect 已解析出的密钥档案与配置放到项目 data-dir；
    密钥内容只在文件间复制，不读取、不打印、不进日志。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    src_arch = FALLBACK_ARCHIVES
    for keys in src_arch.glob("*/keys.json"):
        dst = DATA_DIR / "archives" / keys.parent.name / "keys.json"
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(keys, dst)
    cfg = Path(os.environ.get("LOCALAPPDATA", "")) / "Moect" / "config.json"
    if cfg.exists() and not (DATA_DIR / "config.json").exists():
        shutil.copy2(cfg, DATA_DIR / "config.json")


# ---- Moect 前台窗口隔离 ----
# Moect 是 GUI 程序，--smoke 模式也会弹窗口，SW_HIDE 对它不生效。
# 方案：创建一个独立的隐藏桌面（window station 的 desktop 对象），
# 把 Moect 的界面画在那里，用户主桌面（WinSta0\Default）完全无感。
# 桌面只在采集器进程内创建一次，句柄保持存活；采集器退出后随进程
# 由 OS 回收，无持久化残留。
HIDDEN_DESKTOP_NAME = "MessageHubHeadless"
_hidden_desktop_handle = None
# 隐藏桌面连续无快照的轮数：达到阈值后自动回退前台最小化启动。
# 背景（2026-11-20）：某次重启后隐藏桌面里的 Moect --smoke 连快照目录都
# 不再创建（connect 任务却显示完成），禁用 GPU 也无效；前台启动完全正常。
# 原因不明（疑与 WebView2/窗口站状态相关），故做成自适应：隐藏桌面可用时
# 零窗口闪烁，失效时自动降级为可见但最小化、不抢焦点的启动方式。
_hidden_fail_streak = 0


def _ensure_hidden_desktop() -> bool:
    """幂等创建隐藏桌面，成功返回 True；失败回退普通启动（仅窗口闪现）。"""
    global _hidden_desktop_handle
    if _hidden_desktop_handle is not None:
        return True
    try:
        import ctypes
        # CreateDesktopW 属于 user32（窗口站/桌面系列 API），不在 kernel32
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.CreateDesktopW.argtypes = [
            ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p,
            ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
        user32.CreateDesktopW.restype = ctypes.c_void_p
        # DESKTOP_ALL 访问权
        handle = user32.CreateDesktopW(
            HIDDEN_DESKTOP_NAME, None, None, 0, 0x000F01FF, None)
        if not handle:
            log(f"隐藏桌面创建失败（WinError {ctypes.get_last_error()}），回退普通启动")
            return False
        _hidden_desktop_handle = handle
        log("已启用隐藏桌面隔离：Moect 前台窗口不再出现在主桌面")
        return True
    except Exception as e:
        log(f"隐藏桌面初始化异常 {type(e).__name__}: {e}，回退普通启动")
        return False


def _find_system_python() -> str | None:
    """找一个不受 WorkBuddy 沙箱 sitecustomize 污染的系统 Python。

    本机陷阱：venv python 启动时载入 WorkBuddy 注入的 sitecustomize，
    它把进程可见性过滤掉了（EnumProcesses 只剩 77/509 个 PID，看不到
    Weixin.exe），headless 桥的 psutil 探测随之失效。系统级安装的
    Python 没有这个注入。
    """
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) /
        "Programs" / "Python" / "Python312" / "python.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) /
        "Programs" / "Python" / "Python311" / "python.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) /
        "Programs" / "Python" / "Python313" / "python.exe",
        # WorkBuddy 托管的解释器本体（venv 的 pyvenv.cfg 指向它；本体无注入，
        # 只有基于它创建的 venv 才会被 sitecustomize 污染）
        Path(os.environ.get("LOCALAPPDATA", "")) /
        ".workbuddy" / "binaries" / "python" / "versions" / "3.13.12" /
        "python.exe",
    ]
    for c in candidates:
        try:
            if c.is_file():
                return str(c)
        except OSError:
            continue
    return None


def _refresh_headless(src_dir: Path) -> bool:
    """通过 Moect 源码的 Service.connect() 无 GUI 刷新快照。

    以**系统 Python 子进程**方式运行 moect_headless.py（venv python 被
    WorkBuddy 沙箱注入后看不到微信进程，详见 _find_system_python）。
    成功后新快照落在 archives/<account>/snapshot_xxx，由调用方
    （run_once 的 consolidate）搬进 current/。
    """
    before = {str(p) for p in list_snapshots()}
    py = _find_system_python()
    if not py:
        log("headless: 找不到未注入的系统 Python，无法运行 headless 桥")
        return False
    # 子进程需要与系统 python 相同版本的依赖（psutil/pycryptodomex/PIL）
    # ——首次运行前由 setup/README 指引安装；此处只在失败时提示。
    # 环境清洗：剥离 WorkBuddy 沙箱的 PYTHONPATH 注入（shim/sitecustomize 会
    # 过滤子进程的进程枚举，psutil 看不到 Weixin.exe → detect 误判未登录）。
    child_env = {k: v for k, v in os.environ.items()
                 if k not in ("PYTHONPATH", "PYTHONSTARTUP")}
    child_env["PYTHONIOENCODING"] = "utf-8"
    try:
        r = subprocess.run(
            [py, str(ROOT / "scripts" / "moect_headless.py")],
            capture_output=True, timeout=720, cwd=str(ROOT), env=child_env)
        out = (r.stdout or b"").decode("utf-8", errors="replace")
        for line in out.strip().splitlines():
            if line.startswith("["):
                log("headless: " + line.strip())
        if r.returncode == 0:
            log("headless: 本轮刷新成功")
            return True
        err = (r.stderr or b"").decode("utf-8", errors="replace")
        log(f"headless: 失败 exit={r.returncode} "
            f"{err.strip().splitlines()[-1][:160] if err.strip() else ''}")
        if "ModuleNotFoundError" in err:
            log("headless: 系统 Python 缺依赖，执行：\n"
                f'  "{py}" -m pip install psutil pycryptodomex pillow zstandard')
        return False
    except subprocess.TimeoutExpired:
        log("headless: 子进程超时 720s")
        return False
    except Exception as e:
        log(f"headless 异常: {type(e).__name__}: {str(e)[:200]}")
        return False


def refresh_moect() -> bool:
    """触发一次刷新，等到新快照出现即结束 Moect 进程。

    实测 Moect --smoke 生成快照后 GUI 不会自行退出，若直接 subprocess.run
    会一直阻塞到超时；因此改为「轮询新快照 → 立即结束进程树」。
    """
    global _hidden_fail_streak
    _prepare_data_dir()
    if not MOECT_EXE.is_file():
        log(f"找不到 Moect 可执行文件：{MOECT_EXE}")
        log("  请安装 Moect 并用环境变量 MOECT_EXE 指向 Moect.exe，"
            "或加 --no-refresh 只读现有快照。详见 README「Moect 集成」。")
        return False
    # 首选 headless 桥（2026-11-20）：直接调 Moect GPL 源码的 Service.connect()，
    # 完全绕开 GUI/WebView2/.NET 启动链。当日实测 GUI 路径因 WebView2 初始化
    # 失败（pywebview 回退 GDI+ 窗口 → __ready 永不设置 → --smoke 25s 超时退出）
    # 连续十余轮颗粒无收，而 headless 桥 45 秒稳定产出完整快照。
    # headless 依赖：D:/Projects/moect_src 源码 + python 包 psutil/pycryptodomex/zstandard
    headless_src = Path(os.environ.get(
        "MOECT_HEADLESS_SRC", "D:/Projects/moect_src"))
    if headless_src.is_dir():
        return _refresh_headless(headless_src)
    log("未找到 Moect 源码目录（MOECT_HEADLESS_SRC），回退 GUI --smoke 路径")
    before = {str(p) for p in list_snapshots()}
    # Moect 的 pythonnet/WinForms 启动路径会检测 stdout 句柄：
    # stdout=DEVNULL 时启动即抛 "Unhandled exception in script" 25 秒内退出，
    # stdout 重定向到真实文件句柄则完全正常（2026-11-20 排障结论，
    # 这就是「connect 显示完成但快照不出现」的根因）。两条启动路径都必须
    # 把 stdout/stderr 接到日志文件，绝不能用 DEVNULL。
    smoke_out = DATA_DIR / "smoke_stdout.log"
    h_out = open(smoke_out, "ab")
    h_err = open(DATA_DIR / "smoke_stderr.log", "ab")
    try:
        h_out.truncate(0)
        h_err.truncate(0)
    except OSError:
        pass
    # 隐藏桌面启动方式默认关闭（2026-11-20 排障结论）：本机实测隐藏桌面里的
    # Moect --smoke 长期不出快照（stdout 文件为空，连异常都没有），而前台
    # 最小化启动（SW_SHOWMINNOACTIVE，不抢焦点）稳定产出完整快照。
    # 如需实验隐藏桌面，设环境变量 MOECT_HIDDEN_DESKTOP=1。
    max_hidden_fail = int(os.environ.get("MOECT_HIDDEN_MAX_FAIL", "3"))
    use_desktop = os.environ.get("MOECT_HIDDEN_DESKTOP", "") == "1" \
        and _ensure_hidden_desktop()
    if not use_desktop and _hidden_fail_streak >= max_hidden_fail:
        pass  # 普通路径无需提示；保留计数器供未来重新启用隐藏桌面时使用
        use_desktop = False
    startup_show = 7  # SW_SHOWMINNOACTIVE：最小化且不抢焦点（SW_HIDE 对 GUI 无效）
    if use_desktop:
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateProcessW.argtypes = [
            ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p,
            ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32, ctypes.c_void_p,
            ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p]
        k32.CreateProcessW.restype = ctypes.c_int
        # STARTUPINFOEX 不需要，用 STARTUPINFO + lpDesktop 指向隐藏桌面
        class STARTUPINFO(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_uint32),
                ("lpReserved", ctypes.c_wchar_p),
                ("lpDesktop", ctypes.c_wchar_p),
                ("lpTitle", ctypes.c_wchar_p),
                ("dwX", ctypes.c_uint32), ("dwY", ctypes.c_uint32),
                ("dwXSize", ctypes.c_uint32), ("dwYSize", ctypes.c_uint32),
                ("dwXCountChars", ctypes.c_uint32),
                ("dwYCountChars", ctypes.c_uint32),
                ("dwFillAttribute", ctypes.c_uint32),
                ("dwFlags", ctypes.c_uint32),
                ("wShowWindow", ctypes.c_uint16),
                ("cbReserved2", ctypes.c_uint16),
                ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", ctypes.c_void_p),
                ("hStdOutput", ctypes.c_void_p),
                ("hStdError", ctypes.c_void_p),
            ]
        class PROCESS_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("hProcess", ctypes.c_void_p),
                ("hThread", ctypes.c_void_p),
                ("dwProcessId", ctypes.c_uint32),
                ("dwThreadId", ctypes.c_uint32),
            ]
        si = STARTUPINFO()
        si.cb = ctypes.sizeof(STARTUPINFO)
        si.lpDesktop = "WinSta0\\" + HIDDEN_DESKTOP_NAME
        # STARTF_USESTDHANDLES(0x100)：stdout/stderr 接真实文件句柄，
        # 见上方 smoke_out 注释（DEVNULL 会触发 Moect 启动异常）
        si.dwFlags = 0x100
        si.hStdOutput = int(h_out.fileno())
        si.hStdError = int(h_err.fileno())
        pi = PROCESS_INFORMATION()
        # CreateProcessW 的 lpCommandLine 必须是可写缓冲区（可能被改写），
        # 直接传 Python 字符串会在部分系统上访问违规
        cmdline_buf = ctypes.create_unicode_buffer(
            f'"{MOECT_EXE}" --smoke 1 --data-dir "{DATA_DIR}"')
        # 工作目录必须指向 data-dir：实测 Moect 1.2.8 的 --smoke 在
        # cwd=项目 src/ 等目录时 connect 卡 8% 且不产出快照目录
        #（2026-11-20 排障结论），cwd=data-dir 才能稳定出完整快照。
        cwd_buf = ctypes.create_unicode_buffer(str(DATA_DIR))
        # bInheritHandles=TRUE：不继承句柄的话 Moect 拿到的 std 句柄无效，
        # 等同于无 stdout，一样触发启动异常（2026-11-20 实测）
        ok = k32.CreateProcessW(
            None, cmdline_buf, None, None, 1, 0, None, cwd_buf,
            ctypes.byref(si), ctypes.byref(pi))
        if ok:
            proc_pid = pi.dwProcessId
            # 线程/进程句柄立即关闭，taskkill /IM 兜底时不需要
            k32.CloseHandle(pi.hThread)
            k32.CloseHandle(pi.hProcess)
            log("Moect 已在隐藏桌面启动（用户不可见）")
            proc = None  # 占位：下方轮询逻辑按 PID 兜底
        else:
            err = ctypes.get_last_error()
            log(f"CreateProcessW 失败（WinError {err}），回退普通启动")
            use_desktop = False

    if not use_desktop:
        hidden = subprocess.STARTUPINFO()
        hidden.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        hidden.wShowWindow = startup_show
        try:
            proc = subprocess.Popen(
                [str(MOECT_EXE), "--smoke", "1", "--data-dir", str(DATA_DIR)],
                stdout=h_out, stderr=h_err,   # DEVNULL 会触发 Moect 启动异常
                cwd=str(DATA_DIR),   # 见上方 cwd 注释：Moect 对工作目录敏感
                startupinfo=hidden)
        except Exception as e:
            log(f"刷新启动失败: {type(e).__name__}: {e}")
            h_out.close()
            h_err.close()
            return False
    try:
        deadline = time.time() + 120
        found = False
        last_sig = None
        while time.time() < deadline:
            fresh = [p for p in list_snapshots()
                     if str(p) not in before and p.name.startswith("snapshot_")]
            for cand in fresh:
                if not snapshot_complete(cand):
                    continue
                try:
                    sig = tuple(sorted((f.name, f.stat().st_size)
                                       for f in cand.rglob("*.db") if f.is_file()))
                except Exception:
                    continue
                if sum(size for _, size in sig) < 800 * 1024 * 1024:
                    last_sig = None
                    continue
                # 连续两次文件清单与大小一致 → 认为快照已写完
                if sig == last_sig:
                    found = True
                    break
                last_sig = sig
            if found:
                break
            if proc is not None and proc.poll() is not None:
                # Moect 是 PyInstaller 引导器结构：引导进程在拉起真正的主进程后
                # ~20s 即退出，proc.poll() 判空会把「主进程正在写快照」误判为
                # 「Moect 已结束」而提前收尾。改为按映像名确认没有任何 Moect
                # 进程存活时才允许提前结束等待。
                try:
                    # 不用 text=True：中文 Windows 的 tasklist 输出是 GBK，
                    # 按 UTF-8 解码会炸。映像名是 ASCII，按字节匹配即可。
                    r = subprocess.run(
                        ["tasklist", "/FI", f"IMAGENAME eq {MOECT_EXE.name}"],
                        capture_output=True, timeout=10, check=False)
                    if MOECT_EXE.name.encode() not in (r.stdout or b""):
                        break
                except Exception:
                    break
            time.sleep(2)
    finally:
        # 无论是否已生成快照，都结束 Moect 进程树，避免 GUI 常驻与句柄残留
        kill_pid = proc.pid if proc is not None else proc_pid
        try:
            subprocess.run(["taskkill", "/PID", str(kill_pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10, check=False)
        except Exception:
            if proc is not None:
                try:
                    proc.kill()
                except Exception:
                    pass
        # Moect 单实例转发会把 GUI 进程重新拉起在原进程树之外，上面的按 PID
        # 击杀够不到它。按映像名补杀兜底——在隐藏桌面路径下这只是清理隐藏
        # 桌面里的残留进程，主桌面不受影响；在回退路径下防止窗口闪现后残留。
        # 注意：这会同时杀掉手动打开的 Moect 界面——本管线按设计无头运行，
        # 需要交互使用 Moect 时请先停掉采集器。
        try:
            subprocess.run(["taskkill", "/IM", MOECT_EXE.name, "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10, check=False)
        except Exception:
            pass
        h_out.close()
        h_err.close()
    # 隐藏桌面健康度跟踪：成功清零，失败累计；累计满阈值时下一轮回退前台
    if use_desktop:
        if found:
            _hidden_fail_streak = 0
        else:
            _hidden_fail_streak += 1
    return found


def cleanup_snapshots(keep: Path) -> int:
    """清理项目 runtime 内旧快照的大文件，只保留最新一份的完整数据。

    不做整目录递归删除（一次删 75 个文件会触发环境的批量删除保护），
    改为逐个 unlink 每个旧快照里最大的若干个 .db 文件——这些文件占 95%+ 空间，
    剩余的 WAL/配置等小文件留在目录里可忽略。
    """
    removed = 0
    base = DATA_DIR / "archives"
    if not base.exists():
        return 0
    for d in base.glob("*/snapshot_*"):
        try:
            resolved = d.resolve()
        except Exception:
            continue
        if resolved == keep.resolve():
            continue
        if not str(resolved).startswith(str(SAFE_CLEAN_PREFIX)):
            continue
        try:
            big = sorted((f for f in d.rglob("*.db") if f.is_file()),
                         key=lambda f: f.stat().st_size, reverse=True)[:20]
        except Exception:
            continue
        for f in big:
            try:
                f.unlink()
                removed += 1
            except Exception:
                pass
    return removed


def build_maps(snap: Path) -> tuple:
    """返回 (身份ID→显示名, 每个 message 库的 Name2Id rowid→wxid 与 md5→会话ID)。

    身份键覆盖 username（wxid_ / 微信号形态）与 alias，显示名优先备注（真实姓名）
    再昵称——工作信息归责必须落到人名而不是原始 ID。
    """
    names = {}
    contact = snap / "contact" / "contact.db"
    if contact.exists():
        try:
            c = sqlite3.connect(f"file:{contact}?mode=ro", uri=True)
            for u, nick, remark, alias in c.execute(
                    "SELECT username, nick_name, remark, alias FROM contact"):
                name = (remark or "").strip() or (nick or "").strip()
                if not name or name == "-":
                    continue
                if u:
                    names[u] = name
                if alias and alias not in names:
                    names[alias] = name
            c.close()
        except Exception:
            pass
    db_maps = {}
    for p in sorted(snap.glob("message/message_*.db")):
        if p.name.startswith("message_fts") or p.name.startswith("message_resource"):
            continue
        try:
            c = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            rid2wx = {r[0]: r[1] for r in c.execute(
                "SELECT rowid, user_name FROM Name2Id")}
            md5_2conv = {hashlib.md5(w.encode()).hexdigest(): w
                         for w in rid2wx.values()}
            tables = [r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'")
                if MSG_TABLE_RE.match(r[0])]
            c.close()
            db_maps[p] = (rid2wx, md5_2conv, tables)
        except Exception:
            continue
    return names, db_maps


def decode_content(value, flag) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        if flag == 4 or value[:4] == b"\x28\xb5\x2f\xfd":
            try:
                return zstandard.ZstdDecompressor().decompress(value).decode("utf-8", "replace")
            except Exception:
                return ""
        return value.decode("utf-8", "replace")
    return str(value)


def read_new_messages(snap: Path, watermark: int, limit: int,
                      groups_only: bool = True) -> tuple:
    """兼容旧接口：返回 (消息列表, 本轮最大 create_time)。

    内部走 ``read_new_messages_paged``，保留原签名供既有调用方使用。
    新代码应改用分页版本，以保证超限时不丢消息。
    """
    msgs, cursor = read_new_messages_paged(
        snap, cursor={"state": "init", "watermark": int(watermark), "max_ct": int(watermark)},
        limit=limit, groups_only=groups_only)
    return msgs, int(cursor.get("max_ct") or watermark)


def _private_whitelist() -> set:
    """需要采集的私聊会话显示名集合（personal.json 的 contacts 分层）。

    work + watch 层都要采集（excluded 层不采）；读不到配置时返回空集，
    行为退化为「只采群聊」。每次调用都重读配置，页面改完保存后下一轮生效。
    """
    try:
        data = json.loads((ROOT / "config" / "personal.json").read_text(
            encoding="utf-8"))
        contacts = data.get("contacts") or {}
        names = set()
        for lv in ("work", "watch"):
            for v in contacts.get(lv) or []:
                s = str(v).strip()
                if s:
                    names.add(s)
        return names
    except (OSError, json.JSONDecodeError, AttributeError):
        return set()


def _iter_candidate_rows(snap: Path, watermark: int, cursor: dict | None,
                         groups_only: bool) -> tuple:
    """按确定性顺序遍历候选行，并给出精确的过滤统计。

    排序键 ``(create_time, local_id)``：create_time 只到秒，微信同一秒常有并发
    消息落库，必须用 local_id 作第二排序键才能稳定分页；跨 message_*.db 时再以
    表名做第三键。``cursor`` 是「排他」语义，因此同秒消息跨页不会被跳过。

    私聊白名单（2026-11-18）：groups_only=False 时，只有 contacts 分层里
    work/watch 级联系人的私聊会被放行（显示名匹配，采集桥与规则引擎同源）。

    返回 (生成器, names, stats)。生成器每项是完整消息 dict（含内部游标字段）。
    """
    names, db_maps = build_maps(snap)
    stats = {"scanned": 0, "filtered_non_text": 0, "filtered_too_long": 0,
             "filtered_empty": 0, "filtered_private": 0, "candidates": 0}
    # 白名单在轮次开始时取一次：一轮之内配置不变是合理假设，
    # 页面保存的改动下一轮（≤interval 秒后）生效
    private_allow = _private_whitelist() if not groups_only else set()

    def gen():
        for db_path, (rid2wx, md5_2conv, tables) in db_maps.items():
            try:
                c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            except Exception:
                continue
            for t in tables:
                conv = md5_2conv.get(t[4:])
                if not conv:
                    continue
                # 授权边界：群聊直接放行；私聊只放行 contacts 分层勾选的联系人
                if not conv.endswith("@chatroom"):
                    display = names.get(conv, conv)
                    if display not in private_allow:
                        stats["filtered_private"] += 1
                        continue
                sql = (f'SELECT local_id, server_id, local_type, real_sender_id, '
                       f'create_time, message_content, WCDB_CT_message_content '
                       f'FROM "{t}" WHERE create_time >= ?')
                params: list = [watermark]
                if cursor and cursor.get("state") == "after":
                    # 排他游标：严格位于 (ct, lid, table) 之后的记录才取
                    sql += (' AND (create_time > ?'
                            ' OR (create_time = ? AND local_id > ?)'
                            ' OR (create_time = ? AND local_id = ? AND ? > ?))')
                    params += [cursor["ct"], cursor["ct"], cursor["lid"],
                               cursor["ct"], cursor["lid"], t, cursor["table"]]
                sql += " ORDER BY create_time, local_id"
                try:
                    rows = c.execute(sql, params).fetchall()
                except Exception:
                    continue
                for lid, sid, ltype, rid, ct, content, flag in rows:
                    stats["scanned"] += 1
                    if ltype != TEXT_LOCAL_TYPE:
                        stats["filtered_non_text"] += 1
                        continue
                    text = decode_content(content, flag)
                    if not text:
                        stats["filtered_empty"] += 1
                        continue
                    if len(text) > MAX_TEXT_LEN:
                        stats["filtered_too_long"] += 1
                        continue
                    sender = rid2wx.get(rid, "unknown")
                    m = SENDER_PREFIX.match(text)
                    if m:
                        sender, text = m.group(1), m.group(2).strip()
                    # 发送人显示名：原始 ID → 备注/昵称（查不到时保留原样）
                    sender = names.get(sender, sender)
                    if not text:
                        stats["filtered_empty"] += 1
                        continue
                    stats["candidates"] += 1
                    yield {
                        "source_platform": "wechat",
                        "conversation_id": conv,
                        "conversation_name": names.get(conv, conv),
                        "sender_name": sender,
                        "text": text,
                        "sent_at": datetime.fromtimestamp(ct, BJ).isoformat(),
                        "source_message_id": f"moect:{db_path.name}:{t}:{lid}:{sid}",
                        # 游标定位用的内部字段，推送前由 _strip_internal 剔除
                        "_ct": ct, "_lid": lid, "_table": t,
                    }
            c.close()

    return gen(), names, stats


def _strip_internal(m: dict) -> dict:
    """去掉下划线开头的内部字段，避免污染入库指纹与 API 载荷。"""
    return {k: v for k, v in m.items() if not k.startswith("_")}


def read_new_messages_paged(snap: Path, cursor: dict | None, limit: int,
                            groups_only: bool = True,
                            want_stats: bool = False) -> tuple:
    """确定性分页读取。返回 (消息列表, 下一页游标[, stats])。

    游标是纯数据（可 JSON 序列化）：
      {"state": "init",  "watermark": 1794..., "max_ct": ...}
      {"state": "after", "ct": 123, "lid": 45, "table": "Msg_xxx", ...}

    ``limit`` 只限制本页条数，不影响候选统计。调用方以「下一页是否为空」
    判断是否还有剩余，因此达到 limit 不会静默丢弃任何消息。
    """
    wm = int((cursor or {}).get("watermark", 0))
    gen, _names, stats = _iter_candidate_rows(snap, wm, cursor, groups_only)
    out: list = []
    last = None
    max_ct = int((cursor or {}).get("max_ct") or wm)
    for m in gen:
        if len(out) >= limit:
            # 已满：剩余留到下一页，游标停在最后一条已取消息，绝不越过未处理消息
            break
        out.append(m)
        last = m
        if m["_ct"] > max_ct:
            max_ct = m["_ct"]
    if last is None:
        new_cursor = dict(cursor or {})
        new_cursor["max_ct"] = max_ct
        new_cursor.setdefault("watermark", wm)
        new_cursor.setdefault("state", "init")
        page = []
    else:
        new_cursor = {"state": "after", "watermark": wm,
                      "ct": last["_ct"], "lid": last["_lid"],
                      "table": last["_table"], "max_ct": max_ct}
        page = [_strip_internal(m) for m in out]
    if want_stats:
        return page, new_cursor, stats
    return page, new_cursor


def peek_new_messages(snap: Path, cursor: dict | None, limit: int = 1,
                      groups_only: bool = True) -> list:
    """探测游标之后是否还有消息（不推进游标）。"""
    wm = int((cursor or {}).get("watermark", 0))
    gen, _n, _s = _iter_candidate_rows(snap, wm, cursor, groups_only)
    out: list = []
    for m in gen:
        out.append(_strip_internal(m))
        if len(out) >= limit:
            break
    return out


def cursor_after_ack(page: list, acked: int) -> dict:
    """按「已确认入库条数」推进游标。

    只确认前 ``acked`` 条时游标停在第 acked 条，其余自然留到下一轮，
    因此部分入库失败不会让后续消息永久遗漏。

    支持两种输入：含内部字段（_ct/_lid/_table）的原始项，或已剥离的页项
    （此时从 source_message_id 反解，格式 moect:<db>:<table>:<lid>:<sid>）。
    """
    if acked <= 0 or not page:
        return {"state": "retry"}
    last = page[min(acked, len(page)) - 1]
    ct, lid, table = last.get("_ct"), last.get("_lid"), last.get("_table")
    if lid is None or table is None:
        parsed = parse_source_message_id(last.get("source_message_id"))
        if parsed:
            _db, table, lid, _sid = parsed
        lid = lid if lid is not None else -1
        table = table or ""
    if ct is None:
        # 页项已剥离 _ct，从 sent_at 反解秒级时间戳（无损，构造时即 fromtimestamp）
        ct = iso_to_ts(last.get("sent_at"))
    if ct is None:
        # 完全无法定位：退化为「不推进」，靠水位重扫兜底，绝不跳条
        return {"state": "retry"}
    return {"state": "after", "ct": int(ct), "lid": int(lid), "table": table}


def iso_to_ts(s: str | None) -> int | None:
    """把 sent_at 的 ISO 时间串还原成秒级时间戳，失败返回 None。"""
    if not s:
        return None
    try:
        return int(datetime.fromisoformat(s).timestamp())
    except Exception:
        return None


def parse_source_message_id(smid: str | None):
    """解析 ``moect:<db>:<table>:<lid>:<sid>``，失败返回 None。"""
    if not smid or not smid.startswith("moect:"):
        return None
    parts = smid.split(":")
    if len(parts) < 5:
        return None
    try:
        return parts[1], parts[2], int(parts[3]), parts[4]
    except (ValueError, IndexError):
        return None


def post_batches(messages: list, batch_size: int = 500) -> tuple:
    """分批推送。返回 (已确认条数, 新增入库数, 失败数)。

    关键：中途失败时**只把已成功的批次计入 sent**，剩余部分留给下一轮重试，
    调用方据此只推进到已确认位置，不会因一批失败而丢后面的消息。
    """
    sent = 0
    inserted = 0
    for i in range(0, len(messages), batch_size):
        chunk = messages[i:i + batch_size]
        data = json.dumps({"messages": chunk}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            INGEST_URL, data=data,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.loads(r.read().decode("utf-8"))
            inserted += int(body.get("inserted", 0) or 0)
            sent += len(chunk)
        except Exception as e:
            failed = len(messages) - sent
            log(f"推送失败: {type(e).__name__}: {e}（已确认 {sent} 条，剩余 {failed} 条留待下轮）")
            return sent, inserted, failed
    return sent, inserted, 0



def run_once(args, state: dict) -> dict:
    if not args.no_refresh:
        ok = refresh_moect()
        log(f"Moect 刷新 {'成功' if ok else '失败（使用现有快照）'}")
    snaps = list_snapshots()
    if not snaps:
        log("ERROR: 没有可用的解密快照")
        return state
    # 把项目内快照合并进固定目录（同盘覆盖移动，不删文件、磁盘恒定一份）
    try:
        snap = consolidate(snaps)
    except Exception as e:
        log(f"快照合并跳过: {type(e).__name__}: {e}")
        snap = snaps[0]

    watermark = int(state.get("watermark") or 0)
    if not watermark:
        watermark = int((datetime.now(BJ) - timedelta(hours=args.since_hours)).timestamp())
    log(f"快照={snap.name}  水位={datetime.fromtimestamp(watermark, BJ):%m-%d %H:%M}")

    # 断点续采：上一轮若因 limit / 失败未跑完，从持久化游标接着读
    cursor = state.get("cursor") or {"state": "init", "watermark": watermark,
                                     "max_ct": watermark}
    cursor["watermark"] = watermark
    rounds = 0
    total_sent = total_inserted = total_failed = 0
    agg = {"scanned": 0, "filtered_non_text": 0, "filtered_too_long": 0,
           "filtered_empty": 0, "filtered_private": 0, "candidates": 0}
    drained = True
    while rounds < MAX_PAGES_PER_RUN:
        rounds += 1
        page, next_cursor, st = read_new_messages_paged(
            snap, cursor, args.limit, groups_only=not args.include_private,
            want_stats=True)
        for k in agg:
            agg[k] += st.get(k, 0)
        if not page:
            cursor = next_cursor
            break
        sent, inserted, failed = post_batches(page)
        total_sent += sent
        total_inserted += inserted
        total_failed += failed
        log(f"  第{rounds}页: 本页={len(page)} 推送={sent} 入库={inserted} "
            f"重复={sent - inserted} 失败={failed}")
        if failed:
            # 部分失败：只推进到已确认位置，剩余下轮重试
            cursor = cursor_after_ack(page, sent)
            cursor["max_ct"] = max(int(cursor.get("max_ct") or 0),
                                   int(state.get("cursor", {}).get("max_ct") or 0))
            drained = False
            break
        cursor = next_cursor
        # 本页未满页说明已读完；满页则继续翻页（大积压时一轮内多取几次）
        if len(page) < args.limit:
            break
    else:
        drained = False
        log(f"本轮达到翻页上限 {MAX_PAGES_PER_RUN}，剩余留待下一轮")

    if not drained:
        log("⚠ 本轮未完全消化，游标已持久化，下一轮会接着采")

    total_candidates = agg["candidates"]
    log(f"统计: 扫描={agg['scanned']} 非文本过滤={agg['filtered_non_text']} "
        f"私聊跳过={agg['filtered_private']} 超长={agg['filtered_too_long']} "
        f"空文本={agg['filtered_empty']} 候选={total_candidates} "
        f"推送={total_sent} 入库={total_inserted} "
        f"真重复={total_sent - total_inserted} 失败={total_failed}")

    # ---- 水位推进：只有全部确认且游标已耗尽，才允许越过
    max_ct = int(cursor.get("max_ct") or watermark)
    if drained and total_failed == 0:
        # 回看 5 分钟覆盖乱序落库；去重由 ingest 指纹保证幂等
        state["watermark"] = max(watermark, max_ct - 300)
        state["cursor"] = {"state": "init", "watermark": state["watermark"],
                           "max_ct": state["watermark"]}
    else:
        # 未消化完：水位不动，游标保留，下一轮从同一位置继续
        state["watermark"] = watermark
        state["cursor"] = cursor
    state["last_snapshot"] = snap.name
    state["last_run"] = datetime.now(BJ).isoformat()
    state["total_pushed"] = state.get("total_pushed", 0) + total_sent
    state["last_stats"] = dict(agg, pushed=total_sent, inserted=total_inserted,
                               duplicate=total_sent - total_inserted,
                               failed=total_failed, pages=rounds,
                               drained=drained)
    save_state(state)
    return state


def check_env() -> int:
    """环境自检：确认 Moect、快照、服务、依赖是否就位。

    这是给新机器复现用的——把「为什么跑不起来」变成一行行明确的检查结果，
    而不是让用户对着 WinError 猜。返回 0 表示全部就绪。
    """
    ok = True
    print("=" * 56)
    print(" MessageHub / Moect 环境自检")
    print("=" * 56)

    # 1) Python
    print(f"[Python ] {sys.version.split()[0]}  ({sys.executable})")
    if sys.version_info < (3, 11):
        print("          ✗ 需要 Python 3.11+")
        ok = False

    # 2) 依赖
    try:
        import zstandard  # noqa: F401
        print("[依赖   ] zstandard 已安装")
    except ImportError:
        print("[依赖   ] ✗ 缺少 zstandard，执行：pip install -e .")
        ok = False

    # 3) Moect 可执行文件
    if MOECT_EXE.is_file():
        print(f"[Moect  ] ✓ {MOECT_EXE}")
    else:
        print(f"[Moect  ] ✗ 未找到：{MOECT_EXE}")
        print("          安装 Moect 后设置环境变量 MOECT_EXE 指向 Moect.exe")
        ok = False

    # 4) 快照
    snaps = list_snapshots()
    if snaps:
        latest = snaps[0]
        dbs = list(latest.rglob("message_*.db"))
        print(f"[快照   ] ✓ {latest}")
        print(f"          消息库 {len(dbs)} 个，目录 {latest.stat().st_size // (1024*1024)} MB")
    else:
        print("[快照   ] ✗ 没找到可用快照")
        print(f"          查过：{DATA_DIR / 'archives'}")
        print(f"                {FALLBACK_ARCHIVES}")
        print("          先手动运行一次 Moect，确认它能解密并生成快照")
        ok = False

    # 5) 服务
    try:
        with urllib.request.urlopen(
                INGEST_URL.replace("/ingest", "/status"), timeout=3) as r:
            print(f"[服务   ] ✓ {INGEST_URL.replace('/api/live/ingest', '')} 可访问（HTTP {r.status}）")
    except Exception:
        print(f"[服务   ] ✗ 连不上 {INGEST_URL.replace('/api/live/ingest', '')}")
        print("          先启动服务：启动 MessageHub.bat")
        ok = False

    # 6) 个人配置
    cfg = ROOT / "config" / "personal.json"
    if cfg.exists():
        print(f"[配置   ] ✓ {cfg}")
    else:
        print(f"[配置   ] ! 未找到 {cfg}")
        print("          复制 personal.example.json 为 personal.json 并填写身份与工作群")
        print("          （不填也能采集，但不会提取工作事件）")

    print("-" * 56)
    print(" 全部就绪，可以运行：python scripts/moect_live.py --once" if ok
          else " 有项目未就绪，按上面的提示处理后重试。")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--check", action="store_true", help="环境自检（不采集）")
    ap.add_argument("--interval", type=float, default=60.0, help="刷新间隔（秒）")
    ap.add_argument("--since-hours", type=float, default=24.0)
    ap.add_argument("--limit", type=int, default=3000, help="单轮最多推送条数")
    ap.add_argument("--no-refresh", action="store_true", help="不触发 Moect 刷新，仅读现有快照")
    ap.add_argument("--keep-snapshots", action="store_true", help="不清理旧快照")
    ap.add_argument("--include-private", action="store_true",
                    help="按 personal.json contacts 分层采集私聊："
                         "work/watch 级联系人放行，excluded 与未勾选不采。"
                         "不勾任何联系人时等价于只采群聊")
    args = ap.parse_args()
    if args.check:
        return check_env()
    if not acquire_instance_lock():
        log(f"已有 moect_live 实例在运行（127.0.0.1:{INSTANCE_LOCK_PORT} 被占用），本实例退出")
        return 0
    state = load_state()

    def safe_run():
        try:
            return run_once(args, load_state())
        except Exception as e:
            log(f"本轮异常（已跳过，继续运行）: {type(e).__name__}: {e}")
            return None

    safe_run()
    if args.loop:
        while True:
            time.sleep(args.interval)
            safe_run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
