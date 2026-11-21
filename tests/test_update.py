"""R1.3 版本检查与更新提示测试。

网络全部 mock，测试绝不真连 GitHub；缓存文件跟随测试库目录自动隔离。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from messagehub import update


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "_cache_path",
                        lambda: tmp_path / "update_check.json")
    yield


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _Client:
    """替换 httpx.Client 的最小桩：按 URL 返回预设响应。"""

    def __init__(self, responses):
        self._responses = responses

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, url):
        for pattern, resp in self._responses.items():
            if pattern in url:
                return resp
        return _Resp(404)


def _patch_httpx(monkeypatch, responses):
    monkeypatch.setattr("httpx.Client", lambda **kw: _Client(responses))


def test_local_version_matches_package():
    """/api/version 报告的本地版本必须来自包版本单一来源。"""
    from messagehub import __version__
    status = update.get_update_status()
    assert status["local_version"] == __version__


def test_release_newer_detected(monkeypatch):
    _patch_httpx(monkeypatch, {
        "releases/latest": _Resp(200, {"tag_name": "v9.9.9",
                                       "html_url": "https://github.com/x/releases/v9.9.9"}),
    })
    status = update.get_update_status(force=True)
    assert status["upstream_version"] == "9.9.9"
    assert status["upstream_type"] == "release"
    assert status["has_update"] is True
    assert status["release_url"].endswith("v9.9.9")


def test_release_same_or_older_not_flagged(monkeypatch):
    """上游版本 ≤ 本地 → 不提示（避免重复安装的人被打扰）。"""
    from messagehub import __version__
    _patch_httpx(monkeypatch, {
        "releases/latest": _Resp(200, {"tag_name": "v" + __version__}),
    })
    assert update.get_update_status(force=True)["has_update"] is False


def test_no_release_falls_back_to_commit(monkeypatch):
    """没发过 Release → 回退 main 最新 commit 短哈希提示"有新提交"。"""
    _patch_httpx(monkeypatch, {
        "releases/latest": _Resp(404),
        "commits": _Resp(200, [{"sha": "abcdef1234567890"}]),
    })
    status = update.get_update_status(force=True)
    assert status["upstream_type"] == "commit"
    assert status["upstream_version"] == "abcdef1"


def test_network_failure_degrades_silently(monkeypatch):
    """GitHub 挂了/无网 → upstream=None, has_update=False，不抛异常。"""

    def boom(**kw):
        raise OSError("network down")

    monkeypatch.setattr("httpx.Client", boom)
    status = update.get_update_status(force=True)
    assert status["upstream_version"] is None
    assert status["has_update"] is False


def test_cache_roundtrip_and_ttl(monkeypatch):
    """第一次结果写缓存；缓存未过期时不发网络请求。"""
    calls = {"n": 0}

    class Counting(_Client):
        def get(self, url):
            calls["n"] += 1
            return super().get(url)

    monkeypatch.setattr(
        "httpx.Client",
        lambda **kw: Counting({"releases/latest": _Resp(
            200, {"tag_name": "v9.9.9"})}))

    update.fetch_remote(force=True)
    assert calls["n"] == 1
    # 第二次走缓存，不再发请求
    update.fetch_remote()
    assert calls["n"] == 1


def test_cache_expired_triggers_refetch(monkeypatch):
    """缓存过期 → 重新查。"""
    import time
    update._save_cache({"upstream_version": "0.0.1", "upstream_type": "release"})
    # 把 fetched_at 拨回 2 小时前
    p = update._cache_path()
    data = json.loads(p.read_text(encoding="utf-8"))
    data["_fetched_at"] = time.time() - 7200
    p.write_text(json.dumps(data), encoding="utf-8")

    _patch_httpx(monkeypatch, {
        "releases/latest": _Resp(200, {"tag_name": "v0.0.1"}),
    })
    status = update.get_update_status()  # 不 force，但缓存已过期
    assert status["upstream_version"] == "0.0.1"


def test_version_tuple_parsing():
    assert update._version_tuple("v0.2.1") == (0, 2, 1)
    assert update._version_tuple("0.1.0") == (0, 1, 0)
    assert update._version_tuple("garbage") is None
    assert update._version_tuple("v1") == (1,)
