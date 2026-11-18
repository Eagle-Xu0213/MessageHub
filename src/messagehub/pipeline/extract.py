"""AI 智能整理引擎：规则提取（保底） + 可插拔 LLM（可选）。

设计（开发交接书 §5）：
- 8 分类：urgent_action/action/delegated_followup/waiting/reply_needed/info_only/noise/uncertain
- 提取：标题、动作、责任关系、负责人、截止时间、需回复、置信度、是否需人工确认
- 不明确一律 None=待确认，绝不编造
- 合并重复通知：相同 (title+deadline+conv) 不重复建任务，累计来源证据
- LLM 失败保留原数据：提取异常不影响消息入库
"""
from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from typing import Optional

# ------------------------- 规则提取 -------------------------

_CAT_URGENT = "urgent_action"
_CAT_ACTION = "action"
_CAT_DELEGATED = "delegated_followup"
_CAT_WAITING = "waiting"
_CAT_REPLY = "reply_needed"
_CAT_INFO = "info_only"
_CAT_NOISE = "noise"
_CAT_UNCERTAIN = "uncertain"

_ROLE_ME = "我需要执行"
_ROLE_SUPERVISE = "我需要监督"
_ROLE_OTHER = "其他人的任务"
_ROLE_UNKNOWN = "待确认"

_DEADLINE_RES = [
    re.compile(r"\d{1,2}月\d{1,2}[日号](?:\s*\d{0,2}[点时:：]\d{0,2})?"),
    re.compile(r"(?:今|明|后|大后)天(?:\s*(?:上午|下午|晚上|中午|早|晚))?\s*\d{0,2}[点时:：]?\d{0,2}"),
    re.compile(r"下?周[一二三四五六日天](?:前|之前)?(?:\s*(?:上午|下午|晚上|中午|早|晚))?\s*\d{0,2}[点时:：]?\d{0,2}"),
    re.compile(r"(?:截止|截至|deadline|ddl)[:：]?\s*[^\s，。;；]{0,24}"),
]
_AT = re.compile(r"@([^\s@，,。:：/]{1,16})")
_COLLECTIVE_OWNER = re.compile(r"(各负责人|负责人|各组组长|组长|各班)")
_OWNER = re.compile(
    r"([^\s，,。:：/你您我]{1,10}?)(?:负责|牵头|处理|跟进|提交|完成|回复|统计|收集|发送|落实|协调|催|汇总|核对|搞定)"
)
_BAD_OWNER = re.compile(
    r"[周月日号点时分天今明后每本下上第紧急通知已请需务必尽收截全各活动材料会议表群部分文档进度环节事情]"
)
_NEED_REPLY = re.compile(r"收到请回复|请回复|回复[我1]|确认一下|收到回复|需要回复|报名接龙|接龙|请确认")
_SUPERVISE = re.compile(r"监督|统筹|督促|跟催|盯进度")
_ME2 = re.compile(r"麻烦你|请你|辛苦你|@我|你[^\s，。:：]{0,6}(?:负责|统筹|监督|处理|提交|完成|跟进|回复|报)")
_PROGRESS = re.compile(r"已(?:经)?(?:汇总|完成|提交|发送|收集|整理|统计|填写|填|交|发|报|处理|核对|落实)")
_ACTION = re.compile(
    r"请|需要|麻烦|记得|务必|尽快|提交|完成|填写|报名|回复|收集|统计|发送|整理"
    r"|汇总|准备|安排|落实|协调|跟进|核对|确认|发布|上报|交办|催|报|负责|搞定|牵头|领取|到场|参加"
)
_NOISE = re.compile(r"^\s*[\+\-\*]?\s*$|收到$|好的$|嗯$|谢谢$|麻烦了$")


def extract_deadline(text: str) -> Optional[str]:
    for pattern in _DEADLINE_RES:
        for m in pattern.finditer(text):
            frag = m.group(0).strip()
            if not frag:
                continue
            if m.start() > 0 and text[m.start() - 1] == "每":
                continue  # 排除「每周五」等周期
            return frag
    return None


def extract_owner(text: str) -> tuple[Optional[str], bool]:
    m = _AT.search(text)
    if m:
        cand = m.group(1)
        # @所有人/@大家/@各位 是泛称，不是具体负责人 → 视为集体，owner 待确认
        if cand in ("所有人", "大家", "各位", "全体成员", "all", "All"):
            return None, True
        return cand, False
    m = _COLLECTIVE_OWNER.search(text)
    if m:
        return m.group(1), True
    for match in _OWNER.finditer(text):
        cand = match.group(1).strip("：:，,。、 ")
        if len(cand) < 2 or len(cand) > 12:
            continue
        if re.search(r"\d", cand):
            continue  # 避免匹配时间片段如「00前」「17点」
        if cand in ("方便", "大家", "各位", "所有人", "同学", "同学们", "你们", "你们大家"):
            continue  # 泛称不是具体负责人
        if _BAD_OWNER.search(cand):
            continue
        return cand, False
    return None, False


def _self_directed(text: str, self_names: list[str]) -> bool:
    if any(n for n in self_names if n and n in text):
        return True
    return bool(_ME2.search(text))


def decide_role(text: str, owner: Optional[str], is_collective: bool, self_names: list[str]) -> str:
    if _SUPERVISE.search(text) and _self_directed(text, self_names):
        return _ROLE_SUPERVISE
    if _self_directed(text, self_names):
        return _ROLE_ME
    if owner and not is_collective:
        return _ROLE_OTHER
    return _ROLE_UNKNOWN


def extract_title(text: str) -> str:
    clean = re.sub(r"【[^】]*】", "", text).strip()
    for sent in re.split(r"[。！？!?\n]+", clean):
        if sent.strip() and _ACTION.search(sent):
            return sent.strip()[:40]
    first = next((s.strip() for s in re.split(r"[。！？!?\n]+", clean) if s.strip()), clean)
    return first[:40]


def classify_category(text: str) -> str:
    if _NOISE.match(text):
        return _CAT_NOISE
    if re.search(r"【紧急】|紧急|立刻|马上|速报", text):
        return _CAT_URGENT
    if _NEED_REPLY.search(text):
        return _CAT_REPLY
    if _SUPERVISE.search(text):
        return _CAT_DELEGATED
    if _ACTION.search(text):
        return _CAT_ACTION
    if re.search(r"已|完成|收到|好|感谢|通知|提醒", text):
        return _CAT_INFO
    return _CAT_UNCERTAIN


def extract_task_dict(text: str, sender_name: str = "", conversation_name: str = "",
                      self_names: Optional[list] = None) -> Optional[dict]:
    """从一条消息文本提取任务候选；不像任务返回 None。"""
    self_names = self_names or []
    text = (text or "").strip()
    if not text:
        return None
    # 纯确认/进度，不建任务（除非包含需回复指令）
    if not _NEED_REPLY.search(text):
        if re.match(r"^(收到|好的|嗯+|知道|明白|已收到|ok|OK|好|了解|没问题)[\s，。！!?．、]*", text, re.I):
            return None
    category = classify_category(text)
    has_action = bool(_ACTION.search(text))
    deadline = extract_deadline(text)
    need_reply = bool(_NEED_REPLY.search(text))
    owner, is_collective = extract_owner(text)
    role = decide_role(text, owner, is_collective, self_names)

    # 进度汇报不是新任务
    if _PROGRESS.search(text) and not _NEED_REPLY.search(text) and not _AT.search(text) \
            and category != _CAT_URGENT:
        return None

    actionable = (
        (category in (_CAT_URGENT, _CAT_ACTION, _CAT_DELEGATED) and has_action)
        or bool(_AT.search(text))
        or need_reply
        or (owner is not None and is_collective)
    )
    if not actionable:
        return None

    if owner is None and role in (_ROLE_ME, _ROLE_SUPERVISE):
        owner = self_names[0] if self_names else "我"

    if has_action and deadline and owner:
        confidence = 0.9
    elif has_action and (deadline or owner or need_reply):
        confidence = 0.6
    else:
        confidence = 0.3

    return {
        "title": extract_title(text),
        "description": text,
        "assignee": owner,
        "my_role": role,
        "deadline": deadline,
        "category": category,
        "confidence": confidence,
        "requires_reply": need_reply,
        "reply_suggestion": "收到，我会处理" if role == _ROLE_ME else None,
        "source_conversation": conversation_name,
        "source_sender": sender_name,
        "status": "uncertain",
    }


def task_fingerprint(t: dict) -> str:
    """任务去重指纹：标题 + 截止 + 会话 + 负责人。用于合并重复通知。"""
    key = "|".join([
        str(t.get("title", ""))[:20],
        str(t.get("deadline") or ""),
        str(t.get("source_conversation") or ""),
        str(t.get("assignee") or ""),
    ])
    return "tk::" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


# ------------------------- 入库（合并 + 证据） -------------------------

def run_extraction(db_path: str, self_names: Optional[list] = None, llm=None) -> dict:
    """扫描尚未处理消息，提取任务，去重合并，落库。返回统计。"""
    self_names = self_names or []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("SELECT id, text, sender_name, conversation_name FROM messages ORDER BY id")
    messages = cur.fetchall()

    created = 0
    merged = 0
    # 已存在任务指纹集合（避免重复建）
    cur.execute("SELECT fingerprint FROM tasks")
    existing_fps = {r[0] for r in cur.fetchall() if r[0]}

    for msg in messages:
        mid = msg["id"]
        text = msg["text"]
        # 优先 LLM（若配置且可用），失败回退规则
        tdict = None
        if llm is not None:
            try:
                tdict = llm.extract(text, msg["sender_name"], msg["conversation_name"], self_names)
            except Exception:
                tdict = None
        if tdict is None:
            tdict = extract_task_dict(text, msg["sender_name"], msg["conversation_name"], self_names)
        if not tdict:
            continue
        # LLM 返回的字段可能不全，这里统一补齐，避免 /api/scan 因 KeyError 500
        tdict = {
            "title": tdict.get("title") or text[:40],
            "description": tdict.get("description") or text,
            "assignee": tdict.get("assignee"),
            "my_role": tdict.get("my_role") or "unknown",
            "deadline": tdict.get("deadline"),
            "category": tdict.get("category") or "other",
            "confidence": tdict.get("confidence", 0.5),
            "requires_reply": bool(tdict.get("requires_reply")),
            "reply_suggestion": tdict.get("reply_suggestion"),
        }
        fp = task_fingerprint(tdict)
        now = datetime.now(timezone.utc).isoformat()
        if fp in existing_fps:
            # 合并：把本条消息追加为证据，不新建任务
            cur.execute("SELECT id, source_message_ids, evidence_text FROM tasks WHERE fingerprint=?", (fp,))
            row = cur.fetchone()
            if row:
                ids = (row["source_message_ids"] or "")
                if str(mid) not in ids.split(","):
                    new_ids = (ids + "," + str(mid)).strip(",")
                    ev = (row["evidence_text"] or "")
                    added = f"\n[{msg['conversation_name']}/{msg['sender_name']}] {text[:80]}"
                    cur.execute(
                        "UPDATE tasks SET source_message_ids=?, evidence_text=?, updated_at=? WHERE id=?",
                        (new_ids, (ev + added).strip(), now, row["id"]),
                    )
                    merged += 1
            continue
        # 新建
        cur.execute(
            """INSERT INTO tasks
               (title, description, assignee, my_role, deadline, category, confidence,
                requires_reply, reply_suggestion, source_message_ids, evidence_text,
                status, fingerprint, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                tdict["title"], tdict["description"], tdict["assignee"], tdict["my_role"],
                tdict["deadline"], tdict["category"], tdict["confidence"],
                tdict["requires_reply"], tdict["reply_suggestion"], str(mid),
                f"[{msg['conversation_name']}/{msg['sender_name']}] {text[:120]}",
                "uncertain", fp, now, now,
            ),
        )
        existing_fps.add(fp)
        created += 1

    conn.commit()
    conn.close()
    return {"created": created, "merged": merged, "processed": len(messages)}
