"""Update a clean MessageHub Git checkout, then restart its local web service."""
from __future__ import annotations
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
STATUS = ROOT / 'runtime' / 'update_apply.json'
FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def report(state, message):
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATUS.with_suffix('.tmp')
    tmp.write_text(json.dumps({'state': state, 'message': message}, ensure_ascii=False), encoding='utf-8')
    tmp.replace(STATUS)


def git(*args, check=True):
    proc = subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True,
                          timeout=120, creationflags=FLAGS)
    if check and proc.returncode:
        raise RuntimeError('Git 操作失败，请检查网络、Git 安装及仓库状态')
    return proc


def apply_update():
    if not (ROOT / '.git').is_dir():
        raise RuntimeError('一键更新需要通过 Git 克隆安装；请按照安装说明更新')
    remote = git('remote', 'get-url', 'origin').stdout.strip().rstrip('/').lower()
    if remote.endswith('.git'):
        remote = remote[:-4]
    if remote not in ('https://github.com/eagle-xu0213/messagehub', 'git@github.com:eagle-xu0213/messagehub'):
        raise RuntimeError('仓库地址不是 MessageHub 官方仓库，已停止更新')
    if git('branch', '--show-current').stdout.strip() != 'main':
        raise RuntimeError('当前不在 main 分支，已停止更新')
    if git('status', '--porcelain', '--untracked-files=normal').stdout.strip():
        raise RuntimeError('项目包含未提交文件，请先备份并处理本地改动')
    report('updating', '正在检查并下载更新')
    git('fetch', 'origin', 'main')
    if git('merge-base', '--is-ancestor', 'HEAD', 'FETCH_HEAD', check=False).returncode:
        raise RuntimeError('本地提交与官方版本的历史存在差异，已停止自动更新。请先核对本地提交。')
    git('merge', '--ff-only', 'FETCH_HEAD')
    report('updating', '正在检查依赖')
    proc = subprocess.run([sys.executable, '-m', 'pip', 'install', '-e', '.'], cwd=ROOT,
                          capture_output=True, timeout=600, creationflags=FLAGS)
    if proc.returncode:
        raise RuntimeError('代码已更新，但依赖安装失败；请检查网络后重新安装依赖')


def main():
    try:
        parent_pid = int(sys.argv[1])
        apply_update()
        report('restarting', '更新完成，正在重启服务')
        time.sleep(1)
        os.kill(parent_pid, signal.SIGTERM)
        for _ in range(100):
            with socket.socket() as probe:
                if probe.connect_ex(('127.0.0.1', 8000)) != 0:
                    break
            time.sleep(.1)
        else:
            raise RuntimeError('旧服务未退出，请手动重启 MessageHub')
        subprocess.Popen([sys.executable, '-m', 'uvicorn', 'messagehub.app:app', '--host',
                          '127.0.0.1', '--port', '8000', '--log-level', 'warning', '--no-use-colors'], cwd=ROOT,
                         creationflags=FLAGS | getattr(subprocess, 'DETACHED_PROCESS', 0), close_fds=True)
        for _ in range(200):
            with socket.socket() as probe:
                if probe.connect_ex(('127.0.0.1', 8000)) == 0:
                    report('done', '更新已完成，服务已启动')
                    return
            time.sleep(.1)
        raise RuntimeError('更新已安装，但服务重启超时。请双击「启动 MessageHub.bat」。')
    except Exception as exc:
        report('error', str(exc) if isinstance(exc, RuntimeError) else '更新失败，请检查 Git 和网络')


if __name__ == '__main__':
    main()
