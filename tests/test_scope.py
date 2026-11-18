"""私聊分级（contacts 三层）与检测范围管理的测试。

隔离原则与全项目一致：MESSAGEHUB_PERSONAL_CONFIG 指向临时配置文件，
绝不读写真实 config/personal.json。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


# ---------------- 夹具：临时 personal.json ----------------

@pytest.fixture()
def tmp_personal(tmp_path, monkeypatch):
    """临时配置文件 + 指向它的环境变量。"""
    cfg = tmp_path / "personal.json"
    base = {
        "identity": {
            "nicknames": ["测试昵称"],
            "real_name": "张三",
            "sender_names": ["测试昵称"],
        },
        "groups": {"work": [], "watch": [], "excluded": []},
        "contacts": {"work": [], "watch": [], "excluded": []},
        "aliases": {},
        "brief": {"persona": "一位大学生"},
    }
    cfg.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("MESSAGEHUB_PERSONAL_CONFIG", str(cfg))
    from messagehub import personal
    personal.reload()
    return cfg


def _write(cfg: Path, data: dict) -> None:
    cfg.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _msg(name, text, sender="李四"):
    return {"id": 1, "sent_at": "2026-11-18T10:00:00+08:00",
            "conversation_name": name, "sender_name": sender, "text": text}


# ---------------- personal 加载 ----------------

def test_contacts_levels_loaded(tmp_personal):
    cfg = tmp_personal
    data = json.loads(cfg.read_text(encoding="utf-8"))
    data["contacts"] = {"work": ["王老师"], "watch": ["小明"],
                        "excluded": ["表哥"]}
    _write(cfg, data)
    from messagehub import personal
    personal.reload()
    assert personal.WORK_CONTACTS == {"王老师"}
    assert personal.WATCH_CONTACTS == {"小明"}
    assert personal.EXCLUDED_CONTACTS == {"表哥"}


def test_contacts_default_empty(tmp_personal):
    from messagehub import personal
    assert personal.WORK_CONTACTS == set()
    assert personal.WATCH_CONTACTS == set()


# ---------------- rules 私聊判定 ----------------

def test_private_work_contact_reports_directly(tmp_personal):
    """工作级联系人：非噪声即报，无 cue 概念。"""
    cfg = tmp_personal
    data = json.loads(cfg.read_text(encoding="utf-8"))
    data["contacts"]["work"] = ["王老师"]
    _write(cfg, data)
    from messagehub import personal
    personal.reload()
    from messagehub.rules import extract_event

    ev = extract_event(_msg("王老师", "明天下午三点开会，别迟到"))
    assert ev is not None
    assert ev["channel"] == "private"
    assert ev["event_type"] in ("assignment", "notice")


def test_private_work_contact_noise_still_filtered(tmp_personal):
    """工作级联系人也过噪声过滤：'收到' '哈哈' 不报。"""
    cfg = tmp_personal
    data = json.loads(cfg.read_text(encoding="utf-8"))
    data["contacts"]["work"] = ["王老师"]
    _write(cfg, data)
    from messagehub import personal
    personal.reload()
    from messagehub.rules import extract_event

    assert extract_event(_msg("王老师", "收到")) is None
    assert extract_event(_msg("王老师", "[动画表情]")) is None


def test_private_watch_contact_requires_task_semantics(tmp_personal):
    """混合级联系人：纯闲聊不报，带任务语义才报。"""
    cfg = tmp_personal
    data = json.loads(cfg.read_text(encoding="utf-8"))
    data["contacts"]["watch"] = ["小明"]
    _write(cfg, data)
    from messagehub import personal
    personal.reload()
    from messagehub.rules import extract_event

    assert extract_event(_msg("小明", "周末去打球吗")) is None
    ev = extract_event(_msg("小明", "麻烦你把周报发我一下"))
    assert ev is not None and ev["channel"] == "private"


def test_private_excluded_contact_never_reports(tmp_personal):
    cfg = tmp_personal
    data = json.loads(cfg.read_text(encoding="utf-8"))
    data["contacts"]["excluded"] = ["表哥"]
    _write(cfg, data)
    from messagehub import personal
    personal.reload()
    from messagehub.rules import extract_event

    assert extract_event(_msg("表哥", "帮忙写个报告出来")) is None


def test_unselected_private_never_reports(tmp_personal):
    """默认保守：没勾选的私聊一律不检测。"""
    from messagehub.rules import extract_event
    assert extract_event(_msg("路人甲", "帮忙把材料交一下截止明天")) is None


def test_group_still_works_with_private_config(tmp_personal):
    """加了 contacts 后群聊原逻辑不受影响。"""
    cfg = tmp_personal
    data = json.loads(cfg.read_text(encoding="utf-8"))
    data["groups"]["work"] = ["工作群甲"]
    data["contacts"]["work"] = ["王老师"]
    _write(cfg, data)
    from messagehub import personal
    personal.reload()
    from messagehub.rules import extract_event

    ev = extract_event(_msg("工作群甲", "@张三 麻烦把材料交一下"))
    assert ev is not None and ev["channel"] == "group"


def test_extract_events_gate_includes_contacts(tmp_personal):
    """只配了 contacts 没配 groups 时，extract_events 不再被空白名单挡住。"""
    cfg = tmp_personal
    data = json.loads(cfg.read_text(encoding="utf-8"))
    data["contacts"]["work"] = ["王老师"]
    _write(cfg, data)
    from messagehub import personal
    personal.reload()
    from messagehub.rules import extract_events

    out = extract_events([_msg("王老师", "明天交材料")])
    assert len(out) == 1 and out[0]["channel"] == "private"


# ---------------- scope 配置读写 ----------------

def test_scope_save_and_reload(tmp_personal):
    from messagehub.routers import scope
    scope._save_config(
        groups={"work": ["工作群甲"], "watch": [], "excluded": ["闲聊群"]},
        contacts={"work": ["王老师"], "watch": ["小明"], "excluded": []},
    )
    data = json.loads(tmp_personal.read_text(encoding="utf-8"))
    assert data["groups"]["work"] == ["工作群甲"]
    assert data["contacts"]["work"] == ["王老师"]
    # reload 后运行时值生效
    from messagehub import personal
    assert personal.WORK_CONTACTS == {"王老师"}


def test_scope_save_preserves_identity(tmp_personal):
    """保存只动 groups/contacts，identity 等段保留。"""
    from messagehub.routers import scope
    scope._save_config(groups={"work": ["甲"]}, contacts={})
    data = json.loads(tmp_personal.read_text(encoding="utf-8"))
    assert data["identity"]["real_name"] == "张三"
    assert data["brief"]["persona"] == "一位大学生"


def test_scope_save_dedup_and_strip(tmp_personal):
    from messagehub.routers import scope
    scope._save_config(
        groups={"work": ["甲", "甲", " ", "乙"], "watch": [], "excluded": []},
        contacts={},
    )
    data = json.loads(tmp_personal.read_text(encoding="utf-8"))
    assert data["groups"]["work"] == ["甲", "乙"]


def test_heuristic_levels(tmp_personal):
    """启发式分级的三种典型输出。"""
    from messagehub.routers.scope import _heuristic_level
    d = {"kind": "contact", "recent_messages": 40, "my_ratio": 0.2,
         "work_keyword_hits": {"提交": 3, "截止": 2, "材料": 1},
         "samples": [], "span_days": 10, "name": "x"}
    assert _heuristic_level(d)[0] == "work"
    d2 = {"kind": "contact", "recent_messages": 40, "my_ratio": 0.5,
          "work_keyword_hits": {"提交": 1}, "samples": [], "span_days": 10,
          "name": "x"}
    assert _heuristic_level(d2)[0] == "watch"
    d3 = {"kind": "contact", "recent_messages": 40, "my_ratio": 0.5,
          "work_keyword_hits": {}, "samples": [], "span_days": 10, "name": "x"}
    assert _heuristic_level(d3)[0] == "excluded"


# ---------------- 采集桥白名单 ----------------

def test_private_whitelist_reading(tmp_path):
    """采集桥的 _private_whitelist 读 work+watch 层，坏 JSON 退化为空集。"""
    import sys
    scripts_dir = str(Path(__file__).resolve().parents[1] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import moect_live

    # 函数读 ROOT/config/personal.json，测试根下建同结构目录
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(exist_ok=True)
    cfg = cfg_dir / "personal.json"
    cfg.write_text(json.dumps({
        "contacts": {"work": ["王老师"], "watch": ["小明"], "excluded": ["表哥"]},
    }, ensure_ascii=False), encoding="utf-8")

    old_root = moect_live.ROOT
    moect_live.ROOT = tmp_path
    try:
        assert moect_live._private_whitelist() == {"王老师", "小明"}
        # 坏 JSON → 空集（退化为只采群聊）
        cfg.write_text("{broken", encoding="utf-8")
        assert moect_live._private_whitelist() == set()
    finally:
        moect_live.ROOT = old_root
