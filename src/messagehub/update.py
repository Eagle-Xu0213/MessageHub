"""版本检查与更新提示（R1.3）：让已安装的实例知道上游有新版本。

问题：装出去的实例是"死"的——上游 GitHub 仓库发了新版本，用户毫无感知，
只能靠偶然刷仓库才发现。本模块用最轻的方式补上这个环：

1. /api/version 返回本地版本 + 上游最新版本（有缓存，1 小时查一次 GitHub）
2. 首页后台异步取更新状态，有新版本时页脚显示一条可关闭的横幅
3. 任何失败（无网/超时/限流）静默降级为"查不到"，绝不影响主功能

上游判定：优先用 GitHub Releases（语义化版本），仓库没发 Release 时
回退到 main 分支最新 commit 短哈希（提示"有新提交"）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from messagehub import __version__

REPO = "Eagle-Xu0213/MessageHub"
RELEASES_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
COMMITS_URL = f"https://api.github.com/repos/{REPO}/commits?per_page=1"
MAIN_URL = f"https://api.github.com/repos/{REPO}/commits/main"

# 检查结果缓存：跟随 DB 目录（测试自动隔离），1 小时过期
_CACHE_TTL = 3600
_START_LOCK = threading.Lock()


def _cache_path() -> Path:
    from messagehub.config import DB_PATH
    return DB_PATH.parent / "update_check.json"


def _local_commit() -> Optional[str]:
    from messagehub.config import BASE_DIR
    try:
        result = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=BASE_DIR,
                                capture_output=True, text=True, timeout=5,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _main_commit(force: bool = False) -> Optional[str]:
    cached = _load_cache() if not force else None
    if cached and cached.get('main_commit'):
        return cached['main_commit']
    sha = None
    try:
        import httpx
        with httpx.Client(timeout=5, headers={'User-Agent': 'MessageHub-update-check',
                                             'Accept': 'application/vnd.github+json'}) as client:
            response = client.get(MAIN_URL)
            if response.status_code == 200:
                sha = response.json().get('sha', '')
    except Exception:
        pass
    if not sha:
        try:
            proc = subprocess.run(['git', 'ls-remote', '--heads',
                                   'https://github.com/Eagle-Xu0213/MessageHub.git', 'main'],
                                  capture_output=True, text=True, timeout=15,
                                  creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                                  env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
            sha = proc.stdout.split()[0] if proc.returncode == 0 and proc.stdout.split() else None
        except (OSError, subprocess.TimeoutExpired):
            pass
    if sha and len(sha) == 40 and all(ch in '0123456789abcdef' for ch in sha):
        _save_cache({'main_commit': sha})
        return sha
    return None


def apply_status() -> Dict[str, Any]:
    from messagehub.config import DB_PATH
    try:
        return json.loads((DB_PATH.parent / 'update_apply.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'state': 'idle'}


def start_update() -> None:
    from messagehub.config import BASE_DIR, DB_PATH
    with _START_LOCK:
        if apply_status().get('state') in ('starting', 'updating', 'restarting'):
            raise RuntimeError('更新正在进行，请稍候')
        status_file = DB_PATH.parent / 'update_apply.json'
        status_file.write_text(json.dumps({'state': 'starting', 'message': '正在启动更新'}), encoding='utf-8')
        flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'DETACHED_PROCESS', 0)
        try:
            subprocess.Popen([sys.executable, str(BASE_DIR / 'scripts' / 'update_worker.py'), str(os.getpid())],
                             cwd=BASE_DIR, creationflags=flags, close_fds=True)
        except OSError:
            status_file.write_text(json.dumps({'state': 'error', 'message': '无法启动更新程序'}), encoding='utf-8')
            raise RuntimeError('无法启动更新程序')


def _load_cache() -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and time.time() - data.get("_fetched_at", 0) < _CACHE_TTL:
            return data
    except (OSError, ValueError):
        pass
    return None


def _save_cache(data: Dict[str, Any]) -> None:
    try:
        data = dict(data, _fetched_at=time.time())
        _cache_path().parent.mkdir(parents=True, exist_ok=True)
        _cache_path().write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass  # 缓存写失败无所谓，下次再查


def _version_tuple(v: str):
    """'v0.2.1' → (2, 1)；解析失败返回 None（调用方放弃比较）。"""
    try:
        return tuple(int(x) for x in v.lstrip("v").split("."))
    except ValueError:
        return None


def fetch_remote(force: bool = False, timeout: float = 5.0) -> Dict[str, Any]:
    """查上游最新版本。force=True 忽略缓存（/api/version?refresh=1 用）。

    返回 {upstream_version, upstream_type, release_url, checked_at}；
    任何失败返回 {"upstream_version": None}，调用方按"查不到"处理。
    """
    if not force:
        cached = _load_cache()
        if cached and cached.get('upstream_version'):
            return {k: v for k, v in cached.items() if not k.startswith("_")}

    result: Dict[str, Any] = {
        "upstream_version": None,
        "upstream_type": None,
        "release_url": f"https://github.com/{REPO}/releases",
        "checked_at": None,
    }
    try:
        import httpx
        headers = {"Accept": "application/vnd.github+json",
                   "User-Agent": "MessageHub-update-check"}
        with httpx.Client(timeout=timeout, headers=headers) as c:
            # 优先 Releases；404（没发过 Release）回退 main 最新 commit
            r = c.get(RELEASES_URL)
            if r.status_code == 200:
                data = r.json()
                tag = (data.get("tag_name") or "").strip()
                if tag:
                    result.update(
                        upstream_version=tag.lstrip("v"),
                        upstream_type="release",
                        release_url=data.get("html_url") or result["release_url"],
                    )
            elif r.status_code == 404:
                rc = c.get(COMMITS_URL)
                if rc.status_code == 200:
                    items = rc.json()
                    if items:
                        sha = (items[0].get("sha") or "")[:7]
                        result.update(
                            upstream_version=sha,
                            upstream_type="commit",
                        )
        if result["upstream_version"]:
            from messagehub.config import now_cn_iso
            result["checked_at"] = now_cn_iso()
            _save_cache(result)
    except Exception:
        pass  # 无网/超时/限流：静默降级，主功能不受影响
    return result


def _commit_relation(local: str, remote: str) -> str:
    """Compare commits already present locally; do not fetch or alter the checkout."""
    from messagehub.config import BASE_DIR
    if local == remote:
        return 'same'
    try:
        def ancestor(first, second):
            result = subprocess.run(['git', 'merge-base', '--is-ancestor', first, second],
                                    cwd=BASE_DIR, capture_output=True, timeout=5,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            return result.returncode
        remote_in_local = ancestor(remote, local)
        if remote_in_local == 0:
            return 'ahead'
        local_in_remote = ancestor(local, remote)
        if local_in_remote == 0:
            return 'behind'
        if remote_in_local == 1 and local_in_remote == 1:
            return 'diverged'
    except (OSError, subprocess.TimeoutExpired):
        pass
    # The remote commit may not exist in the local object database yet.
    return 'unknown'


def get_update_status(force: bool = False) -> Dict[str, Any]:
    """本地 vs 上游比对，给前端/首页用的最终形态。"""
    local_commit = _local_commit()
    if local_commit:
        remote_commit = _main_commit(force=force)
        relation = _commit_relation(local_commit, remote_commit) if remote_commit else 'unknown'
        return {'local_version': __version__, 'local_commit': local_commit[:7],
                'upstream_version': remote_commit[:7] if remote_commit else None,
                'upstream_type': 'commit',
                'has_update': bool(remote_commit and relation in ('behind', 'diverged', 'unknown')),
                'can_update': sys.platform == 'win32' and relation != 'diverged',
                'relation': relation, 'checked': bool(remote_commit),
                'release_url': f'https://github.com/{REPO}/commits/main'}
    remote = fetch_remote(force=force)
    remote_ver = remote.get("upstream_version")
    has_update = False
    if remote_ver:
        if remote.get("upstream_type") == "release":
            local, up = _version_tuple(__version__), _version_tuple(remote_ver)
            has_update = bool(local and up and up > local)
        else:  # commit 型：短哈希不同即提示（粗粒度，宁可多提醒）
            has_update = remote_ver != __version__
    return {
        "local_version": __version__,
        "upstream_version": remote_ver,
        "upstream_type": remote.get("upstream_type"),
        "release_url": remote.get("release_url"),
        "has_update": has_update,
        "can_update": False,
    }
