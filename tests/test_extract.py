"""AI 整理引擎测试：规则提取 + 合并 + 证据 + LLM 回退"""
import sys
import os
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from messagehub.app import app, get_db, init_db
from messagehub.pipeline.extract import (
    extract_task_dict, run_extraction, task_fingerprint, extract_deadline, extract_owner
)
from messagehub.pipeline.importer import import_file
from messagehub.llm.provider import LLMProvider

DB_PATH = str(os.environ.get("MESSAGEHUB_DB_PATH") or (Path(__file__).parent.parent / "runtime" / "messagehub.db"))
TMP = Path(__file__).parent.parent / "runtime" / "test_tmp"


@pytest.fixture(autouse=True)
def reset():
    init_db()
    conn = get_db()
    cur = conn.cursor()
    for t in ("messages", "tasks", "capture_runs", "scan_state"):
        cur.execute(f"DELETE FROM {t}")
    conn.commit()
    conn.close()
    if TMP.exists():
        shutil.rmtree(TMP)
    TMP.mkdir(parents=True, exist_ok=True)
    yield
    if TMP.exists():
        shutil.rmtree(TMP)


def _import_texts(texts):
    watch = TMP / "msgs"
    watch.mkdir(exist_ok=True)
    from messagehub.pipeline.watcher import scan_directory
    f = watch / "m.jsonl"
    import json
    lines = []
    for i, t in enumerate(texts):
        lines.append(json.dumps({
            "source_platform": "wechat", "conversation_id": "g1",
            "conversation_name": "办公室群", "sender_name": "主任",
            "sent_at": f"2026-11-13T09:0{i}:00+08:00", "text": t,
            "source_message_id": f"m{i}",
        }, ensure_ascii=False))
    f.write_text("\n".join(lines), encoding="utf-8")
    return scan_directory(DB_PATH, str(watch))


def test_extract_deadline():
    assert extract_deadline("请今天17:00前提交总结") == "今天17:00"
    assert extract_deadline("下周五前交表") == "下周五前"
    # 每周五不是截止
    assert extract_deadline("每周五开会") is None


def test_extract_owner_at():
    owner, coll = extract_owner("@张三 请负责统计")
    assert owner == "张三"
    assert coll is False


def test_extract_task_basic():
    t = extract_task_dict("【紧急】请各位今天17:00前提交本月工作总结", "主任", "办公室群", ["我"])
    assert t is not None
    assert t["category"] == "urgent_action"
    assert t["deadline"] is not None
    assert t["my_role"] in ("我需要执行", "待确认")


def test_extract_task_no_action_returns_none():
    t = extract_task_dict("今天天气不错", "同学", "闲聊群")
    assert t is None


def test_run_extraction_creates_and_merges():
    _import_texts([
        "【紧急】请今天17:00前提交总结",
        "【紧急】请今天17:00前提交总结",  # 重复通知
        "收到，马上提交",  # 进度汇报，不应建任务
    ])
    stats = run_extraction(DB_PATH, self_names=["我"])
    assert stats["created"] == 1  # 两条相同只建 1 个
    assert stats["merged"] == 1   # 第二条合并
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM tasks")
    assert cur.fetchone()[0] == 1
    cur.execute("SELECT source_message_ids FROM tasks")
    ids = cur.fetchone()[0]
    assert len(ids.split(",")) == 2  # 证据含两条来源


def test_extract_llm_fallback_when_disabled():
    llm = LLMProvider(api_key=None, enabled=False)
    assert llm.enabled is False
    # extract 应返回 None（无 key），调用方回退规则
    assert llm.extract("测试", "s", "c", []) is None
