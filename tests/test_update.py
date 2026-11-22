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
    monkeypatch.setattr(update, "_local_commit", lambda: None)
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


def test_git_version_compares_actual_commit(monkeypatch):
    monkeypatch.setattr(update, '_local_commit', lambda: 'a' * 40)
    monkeypatch.setattr(update, '_main_commit', lambda force=False: 'a' * 40)
    assert update.get_update_status()['has_update'] is False
    monkeypatch.setattr(update, '_main_commit', lambda force=False: 'b' * 40)
    assert update.get_update_status()['has_update'] is True
    monkeypatch.setattr(update, '_main_commit', lambda force=False: None)
    assert update.get_update_status()['has_update'] is False


def test_worker_updates_clean_repo_and_rejects_local_changes(tmp_path, monkeypatch):
    import importlib.util
    import subprocess
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location('update_worker', root / 'scripts/update_worker.py')
    worker = importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
    def run(*args, cwd):
        return subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, check=True)
    upstream = tmp_path / 'upstream'; upstream.mkdir()
    run('init', '-b', 'main', cwd=upstream)
    run('config','user.email','test@example.invalid',cwd=upstream)
    run('config','user.name','Test',cwd=upstream)
    (upstream/'item.txt').write_text('old')
    run('add','item.txt',cwd=upstream); run('commit','-m','initial',cwd=upstream)
    checkout = tmp_path/'checkout'
    run('clone',str(upstream),str(checkout),cwd=tmp_path)
    (upstream/'item.txt').write_text('new')
    run('commit','-am','update',cwd=upstream)
    monkeypatch.setattr(worker,'ROOT',checkout)
    monkeypatch.setattr(worker,'STATUS',checkout/'runtime'/'update_apply.json')
    # runtime files are ignored in real installs.
    (checkout/'.git'/'info'/'exclude').write_text('runtime/\n')
    real_git = worker.git
    def official(*args, **kwargs):
        if args == ('remote','get-url','origin'):
            return subprocess.CompletedProcess(args, 0, 'https://github.com/Eagle-Xu0213/MessageHub.git\n', '')
        return real_git(*args, **kwargs)
    monkeypatch.setattr(worker,'git',official)
    real_run = worker.subprocess.run
    def no_pip(args, *a, **k):
        if '-m' in args and 'pip' in args:
            return subprocess.CompletedProcess(args, 0)
        return real_run(args, *a, **k)
    monkeypatch.setattr(worker.subprocess,'run',no_pip)
    worker.apply_update()
    assert (checkout/'item.txt').read_text() == 'new'
    (checkout/'item.txt').write_text('local edits')
    with pytest.raises(RuntimeError, match='未提交'):
        worker.apply_update()
    assert (checkout/'item.txt').read_text() == 'local edits'


def test_main_commit_falls_back_to_git_when_api_unavailable(monkeypatch):
    import subprocess
    def offline(**kwargs):
        raise OSError('unavailable')
    monkeypatch.setattr('httpx.Client', offline)
    monkeypatch.setattr(update.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0, 'a'*40+'\trefs/heads/main\n', ''))
    assert update._main_commit(force=True) == 'a' * 40


def test_commit_relation_tracks_ahead_behind_and_divergence(tmp_path, monkeypatch):
    import subprocess
    from messagehub import config

    def git(*args):
        return subprocess.run(['git', *args], cwd=tmp_path, capture_output=True,
                              text=True, check=True).stdout.strip()

    git('init', '-b', 'main')
    git('config', 'user.email', 'test@example.invalid')
    git('config', 'user.name', 'Test')
    git('commit', '--allow-empty', '-m', 'base')
    base = git('rev-parse', 'HEAD')
    git('commit', '--allow-empty', '-m', 'local')
    local = git('rev-parse', 'HEAD')
    git('checkout', '-b', 'other', base)
    git('commit', '--allow-empty', '-m', 'remote')
    other = git('rev-parse', 'HEAD')
    monkeypatch.setattr(config, 'BASE_DIR', tmp_path)
    assert update._commit_relation(local, local) == 'same'
    assert update._commit_relation(local, base) == 'ahead'
    assert update._commit_relation(base, local) == 'behind'
    assert update._commit_relation(local, other) == 'diverged'
    assert update._commit_relation(local, 'f' * 40) == 'unknown'


def test_ahead_checkout_has_no_false_update(monkeypatch):
    monkeypatch.setattr(update, '_local_commit', lambda: 'a' * 40)
    monkeypatch.setattr(update, '_main_commit', lambda force=False: 'b' * 40)
    monkeypatch.setattr(update, '_commit_relation', lambda local, remote: 'ahead')
    status = update.get_update_status()
    assert status['relation'] == 'ahead'
    assert status['has_update'] is False


def test_diverged_checkout_does_not_offer_one_click_update(monkeypatch):
    monkeypatch.setattr(update, '_local_commit', lambda: 'a' * 40)
    monkeypatch.setattr(update, '_main_commit', lambda force=False: 'b' * 40)
    monkeypatch.setattr(update, '_commit_relation', lambda local, remote: 'diverged')
    status = update.get_update_status()
    assert status['has_update'] is True
    assert status['can_update'] is False
