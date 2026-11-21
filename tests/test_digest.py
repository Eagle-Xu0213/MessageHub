"""唤醒补扫 + 加急分流引擎测试。"""
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from messagehub import digest
from messagehub.config import CN_TZ


def _ts(minutes_ago=0):
    return (datetime.now(CN_TZ) - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")


def test_state_isolated_and_roundtrip(tmp_path, monkeypatch):
    """状态文件跟随 DB 目录，测试绝不写生产 runtime。"""
    fake = tmp_path / "digest_state.json"
    monkeypatch.setattr(digest, "STATE_FILE", fake)
    s = digest.load_state()
    assert s["last_digest_at"] is None
    s["last_digest_at"] = "2026-11-20T10:00:00+08:00"
    s["urgent_sent_ids"] = [1, 2]
    digest.save_state(s)
    assert digest.load_state()["urgent_sent_ids"] == [1, 2]


def test_is_urgent_requires_all_three():
    """三者必须同时满足：直呼 + 强指派 + 临近截止。"""
    base = {
        "cue_type": "at_me", "work_score": 2, "event_type": "assignment",
        "deadline": "明天下午3点前", "text": "明天下午3点前把材料交给我",
        "sent_at": _ts(), "reply_status": "unanswered",
    }
    ok, _ = digest.is_urgent(base, llm=None)
    assert ok is True

    no_cue = dict(base, cue_type=None)
    assert digest.is_urgent(no_cue, llm=None)[0] is False

    weak = dict(base, work_score=1)
    assert digest.is_urgent(weak, llm=None)[0] is False

    resolved = dict(base, reply_status="answered_ok")
    assert digest.is_urgent(resolved, llm=None)[0] is False

    no_ddl = dict(base, deadline="", text="这条没有时间信息")
    assert digest.is_urgent(no_ddl, llm=None)[0] is False


def test_is_urgent_no_false_positive_on_plain_chat():
    """普通聊天（无 cue、无指派）绝不触发加急。"""
    ev = {
        "cue_type": None, "work_score": 0, "event_type": "notice",
        "deadline": "明天", "text": "明天天气不错", "sent_at": _ts(),
        "reply_status": "unanswered",
    }
    assert digest.is_urgent(ev, llm=None)[0] is False


def test_tag_events_fallback_uses_group(tmp_path, monkeypatch):
    """LLM 不可用时标签降级为群名，不抛异常。"""
    events = [{"conversation_name": "课程群", "text": "明天交作业", "message_id": 1}]
    out = digest.tag_events(events, llm=None)
    assert out[0]["semantic_tag"] == "课程群"
    assert out[0].get("tag_degraded") is True


def test_group_by_tag(tmp_path):
    events = [
        {"semantic_tag": "课程作业", "message_id": 1},
        {"semantic_tag": "课程作业", "message_id": 2},
        {"semantic_tag": "会议安排", "message_id": 3},
    ]
    groups = digest.group_by_tag(events)
    assert list(groups.keys()) == ["课程作业", "会议安排"]
    assert len(groups["课程作业"]) == 2


def test_run_catchup_no_events_is_silent(tmp_path, monkeypatch):
    """无事件时不发信、不推进水位（静默）。"""
    monkeypatch.setattr(digest, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(digest, "collect_events_since", lambda *a, **k: [])
    result = digest.run_catchup(send_mail=False)
    assert result["sent"] is False
    assert "静默" in (result.get("skipped") or "")
    assert digest.load_state()["last_digest_at"] is None


def test_run_catchup_advances_watermark_only_on_success(tmp_path, monkeypatch):
    """发信成功才推进水位；预览模式（send_mail=False）也推进（用于手动调试）。"""
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(digest, "STATE_FILE", state_file)
    ev = {
        "message_id": 10, "sent_at": _ts(), "conversation_name": "课程群",
        "sender_name": "老师", "text": "明天交作业", "event_type": "assignment",
        "cue_type": "at_me", "work_score": 2, "deadline": "明天",
        "reply_status": "unanswered",
    }
    monkeypatch.setattr(digest, "collect_events_since", lambda *a, **k: [ev])
    monkeypatch.setattr(digest, "tag_events", lambda evs, llm: [
        dict(e, semantic_tag="课程作业") for e in evs])
    result = digest.run_catchup(send_mail=False)
    assert result["event_count"] == 1
    assert result["groups"] == {"课程作业": 1}
    assert digest.load_state()["last_digest_at"] is not None


def test_catchup_excludes_already_urgent_events(tmp_path, monkeypatch):
    """已加急发过的事件不再进常规汇总，避免同一件事打扰两次。"""
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(digest, "STATE_FILE", state_file)
    # 预置：message_id=99 已加急发过
    st = digest._empty_state()
    st["urgent_sent_ids"] = [99, "99"]
    digest.save_state(st)

    normal = {
        "message_id": 10, "sent_at": _ts(), "conversation_name": "课程群",
        "sender_name": "老师", "text": "普通作业提醒", "event_type": "notice",
        "reply_status": "unanswered",
    }
    already_urgent = dict(normal, message_id=99, text="这条已加急发过")
    monkeypatch.setattr(
        digest, "collect_events_since", lambda *a, **k: [normal, already_urgent])
    monkeypatch.setattr(digest, "tag_events", lambda evs, llm: [
        dict(e, semantic_tag="课程作业") for e in evs])

    result = digest.run_catchup(send_mail=False)
    assert result["event_count"] == 1, "已加急的那条应被排除"
    assert result["deduped_urgent_count"] == 1
    assert result["skipped"] is None


def test_catchup_kind_recorded_in_state(tmp_path, monkeypatch):
    """kind 区分补扫/汇总，并记录到状态轨迹，便于排障。"""
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(digest, "STATE_FILE", state_file)
    ev = {
        "message_id": 11, "sent_at": _ts(), "conversation_name": "项目群",
        "sender_name": "同事", "text": "更新一下进度", "event_type": "notice",
        "reply_status": "unanswered",
    }
    monkeypatch.setattr(digest, "collect_events_since", lambda *a, **k: [ev])
    monkeypatch.setattr(digest, "tag_events", lambda evs, llm: [
        dict(e, semantic_tag="项目进度") for e in evs])

    r = digest.run_catchup(send_mail=False, kind="hourly")
    assert r["kind"] == "hourly"
    assert digest.load_state()["last_digest_kind"] == "hourly"


def test_urgent_dedup(tmp_path, monkeypatch):
    """同一条加急消息只发一次（message_id 去重）。"""
    state_file = tmp_path / "state.json"
    monkeypatch.setattr(digest, "STATE_FILE", state_file)
    ev = {
        "message_id": 99, "sent_at": _ts(), "conversation_name": "项目群",
        "sender_name": "领导", "text": "明天下午3点前把方案发我",
        "event_type": "assignment", "cue_type": "at_me", "work_score": 2,
        "deadline": "明天下午3点前", "reply_status": "unanswered",
    }
    monkeypatch.setattr(digest, "collect_events_since", lambda *a, **k: [ev])
    monkeypatch.setattr(digest.is_urgent, "__defaults__", None) if False else None

    calls = {"n": 0}

    def fake_mail(payload):
        calls["n"] += 1
        return {"sent": True}

    import messagehub.mail as mail
    import messagehub.desktop_notify as dn
    monkeypatch.setattr(mail, "send_urgent_email", fake_mail)
    monkeypatch.setattr(dn, "notify_urgent", lambda ev: True)

    r1 = digest.run_urgent_check(send=True)
    r2 = digest.run_urgent_check(send=True)
    assert r1["urgent_count"] == 1
    assert r2["urgent_count"] == 0, "已发过的加急消息不应再次触发"
    assert calls["n"] == 1


# ---------------- R1.2 LLM 闭环复核 ----------------

class _FakeLLM:
    """模拟 LLMProvider：enabled 开关可控，chat_json 返回预设结果。"""

    def __init__(self, data=None, enabled=True):
        self.enabled = enabled
        self.data = data
        self.calls = []

    def chat_json(self, prompt, timeout=60.0, **kw):
        self.calls.append(prompt)
        return self.data


def test_llm_verify_marks_semantically_done(monkeypatch):
    """规则判 unanswered + LLM 判 done → answered_ok，且 evidence 落地。"""
    messages = [
        {"id": 1, "sent_at": "2026-11-20T10:00+08:00", "conversation_name": "群",
         "sender_name": "老师", "text": "把实验数据发我"},
        {"id": 2, "sent_at": "2026-11-20T10:01+08:00", "conversation_name": "群",
         "sender_name": "老师", "text": "在吗"},
    ]
    ev = {"message_id": 1, "conversation_name": "群", "event_type": "assignment",
          "text": "把实验数据发我", "reply_status": "unanswered"}
    llm = _FakeLLM(data={"results": [True]})
    out = digest.llm_verify_resolution([ev], messages, llm)
    assert out[0]["reply_status"] == "answered_ok"
    assert "LLM" in (out[0]["reply_evidence"] or "")


def test_llm_verify_keeps_unanswered_when_llm_says_no():
    """LLM 判未办结 → 保持 unanswered，继续提醒。"""
    messages = [
        {"id": 1, "sent_at": "2026-11-20T10:00+08:00", "conversation_name": "群",
         "sender_name": "老师", "text": "明天交材料"},
    ]
    ev = {"message_id": 1, "conversation_name": "群", "event_type": "assignment",
          "text": "明天交材料", "reply_status": "unanswered"}
    llm = _FakeLLM(data={"results": [False]})
    out = digest.llm_verify_resolution([ev], messages, llm)
    assert out[0]["reply_status"] == "unanswered"


def test_llm_verify_degrades_gracefully(monkeypatch):
    """LLM 挂了/输出坏 JSON → 原样保留 unanswered，绝不误标已办结。"""
    messages = [
        {"id": 1, "sent_at": "2026-11-20T10:00+08:00", "conversation_name": "群",
         "sender_name": "老师", "text": "明天交材料"},
    ]
    ev = {"message_id": 1, "conversation_name": "群", "event_type": "assignment",
          "text": "明天交材料", "reply_status": "unanswered"}

    for bad in (_FakeLLM(data=None),                      # 调用失败
                _FakeLLM(data={"results": "bad"}),        # 形状不对
                _FakeLLM(data={"results": [True, False]}),  # 长度不匹配
                _FakeLLM(enabled=False)):                 # LLM 未配置
        copy = dict(ev)
        out = digest.llm_verify_resolution([copy], messages, bad)
        assert out[0]["reply_status"] == "unanswered", f"降级失败: {bad.data}"


def test_llm_verify_skips_already_resolved():
    """规则已判闭环的事件不进复核（只处理 unanswered）。"""
    messages = []
    ev = {"message_id": 1, "conversation_name": "群", "event_type": "assignment",
          "text": "x", "reply_status": "answered_ok"}
    llm = _FakeLLM(data={"results": []})
    out = digest.llm_verify_resolution([ev], messages, llm)
    assert out[0]["reply_status"] == "answered_ok"
    assert llm.calls == [], "没有 unanswered 事件时不应发起 LLM 调用"


def test_collect_events_since_passes_llm_through(monkeypatch):
    """collect_events_since 的 llm 参数透传给复核函数。"""
    seen = {}

    def fake_verify(events, msgs, llm):
        seen["llm"] = llm
        return events

    msgs = [{"id": 1, "sent_at": "2026-11-20T10:00+08:00", "conversation_name": "群",
             "sender_name": "老师", "text": "hi"}]
    monkeypatch.setattr(digest, "_load_messages_since", lambda s, d: msgs)
    monkeypatch.setattr(digest, "llm_verify_resolution", fake_verify)
    llm = _FakeLLM()
    digest.collect_events_since(None, llm=llm)
    assert seen["llm"] is llm
