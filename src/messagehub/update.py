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
import time
from pathlib import Path
from typing import Any, Dict, Optional

from messagehub import __version__

REPO = "Eagle-Xu0213/MessageHub"
RELEASES_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
COMMITS_URL = f"https://api.github.com/repos/{REPO}/commits?per_page=1"

# 检查结果缓存：跟随 DB 目录（测试自动隔离），1 小时过期
_CACHE_TTL = 3600


def _cache_path() -> Path:
    from messagehub.config import DB_PATH
    return DB_PATH.parent / "update_check.json"


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
        if cached:
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


def get_update_status(force: bool = False) -> Dict[str, Any]:
    """本地 vs 上游比对，给前端/首页用的最终形态。"""
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
    }
