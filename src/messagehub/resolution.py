"""事件闭环检测（R1.1）：分析事件后用户自己的回复，避免孤立报事。

问题：识别到任务后如果只看单条消息，会孤立分析。例如「@我 今晚能过来吗」
→ 用户已回「今晚不行，明天可以」→ 该事件应标记为已应承（改期），
而不是作为"待回复"重复提醒。

答复分类（reply_status）：
  answered_ok     已应承——用户明确同意/确认（好/行/可以/收到/嗯/会去…）
  answered_no     已婉拒——用户明确拒绝（不行/没空/不去/来不了…）
  answered_alt    改期/替代方案——拒绝+给出新时间（明天晚上应该可以）
  unanswered      未答复——窗口内无用户发言或发言与事件无关

判定窗口：事件后同群 N 条消息内（默认 15 条）或至下一位非用户消息打破话题。
实现是启发式的：先找窗口内用户发言，再按词表分类；找不到就 unanswered。

「用户本人」的判定依赖 personal.json 的 identity.sender_names；
未配置时所有事件都归为 unanswered（宁可多提醒，不可假装已处理）。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from messagehub import personal
from messagehub.config import CN_TZ

MY_SENDER_NAMES = personal.MY_SENDER_NAMES

# 明确同意/确认词（独立短句或句首）。单字"哦哦哦"类不算——那是对闲聊的应答
_ACK = re.compile(
    r"^(?:好的?嘞?哒?|行(?:吧|的)?|可以(?:啊|的)?|没问题|收到|嗯+(?:嗯+)*好?|ok|OK|OKK|Okk|"
    r"明白|了解|知道了?|会去|去|来|安排上|肯定|必须|那行|那就这样|"
    r"我去|我来|我来搞|交给我|包在我身上)(?:[\s，,。！!~\[].*)?$"
)
# 多字应答："好的""嗯嗯""可以呀""好呀"（排除单字"哦"）
_ACK_SHORT = re.compile(r"^(?:嗯{2,}|好呀|好哒|好嘞|行呀|可以呀|没问题呀|OK呀|欧克)[\s！!~。.]*$")
# 承诺新时间："明天晚上应该可以""后天可以""下周三行"——拒绝后的替代承诺
_ALT_COMMIT = re.compile(
    r"^(?:明天|后天|大后天|下?周[一二三四五六日天]|\d{1,2}[点:：])[^。！!？?]{0,14}"
    r"(?:应该)?(?:可以|行|没问题|有空|能|来|去|到)[\s！!~。.]*$"
)
# 明确拒绝词
_DECLINE = re.compile(
    r"(?:不行|没空|来不了|去不了|没时间|有事|有课|赶不过去|去不成|暂?时不?行|"
    r"恐怕不行|去不了了|过不去|缺席|请假)")
# 改期/替代提示（拒绝+新时间 = answered_alt）
_ALT_TIME = re.compile(
    r"(?:明天|后天|大后天|下[周一二三四五六日天]|周[一二三四五六日天]|晚上?|中午|下午|上午|"
    r"\d{1,2}[点:：]\d{0,2}|\d{1,2}月\d{1,2}[日号]|改天|下次|晚点|待会|回头)")
# 用户发起的执行确认（说明已在推进）
_DOING = re.compile(r"(?:已经(?:发|交|传|完成|搞定|弄好|做好了?)|搞定|弄好了?|发过了?|交了|传了|做好了?)")


def _parse_sent_at(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _classify_reply(text: str) -> Optional[str]:
    """对用户单条回复分类：ack / decline / alt / doing / None(无关)。"""
    t = (text or "").strip()
    if not t:
        return None
    if _DOING.search(t):
        return "doing"
    if _DECLINE.search(t):
        if _ALT_TIME.search(t):
            return "alt"
        return "decline"
    if _ALT_COMMIT.match(t):
        return "alt"
    if _ACK.match(t) or _ACK_SHORT.match(t):
        return "ack"
    return None


def analyze_resolution(event: Dict[str, Any],
                       group_messages: List[Dict[str, Any]],
                       window: int = 15) -> Dict[str, Any]:
    """分析单条事件的闭环状态。

    group_messages：事件所在群按时间升序的全部消息（含事件本身）。
    返回 {reply_status, reply_evidence}；reply_evidence 合并窗口内我的连续答复
    （最多 3 条），改期类取最后一条含新时间的回复，避免只截到「不行」漏掉「明天可以」。
    """
    ev_idx = None
    ev_msg_id = event.get("message_id")
    for i, m in enumerate(group_messages):
        if m.get("id") == ev_msg_id:
            ev_idx = i
            break
    if ev_idx is None:
        return {"reply_status": "unanswered", "reply_evidence": None}

    status = "unanswered"
    evidence_parts: List[str] = []
    seen = 0
    for m in group_messages[ev_idx + 1:]:
        seen += 1
        if seen > window:
            break
        sender = (m.get("sender_name") or "").strip()
        if sender not in MY_SENDER_NAMES:
            continue
        text_raw = (m.get("text") or "").strip()
        cls = _classify_reply(text_raw)
        if cls is None:
            continue
        text_snip = text_raw[:60]
        if cls == "doing":
            status, evidence_parts = "answered_ok", [text_snip]
            break
        if cls == "alt":
            # 改期：继续收集后续答复（「明天晚上应该可以」常在拒绝后一两条）
            status = "answered_alt"
            evidence_parts.append(text_snip)
            continue
        if cls == "decline":
            # 先拒绝：暂存，若窗口内后续出现 alt/ack 则覆盖
            if status == "unanswered":
                status, evidence_parts = "answered_no", [text_snip]
            continue
        if cls == "ack":
            if status in ("unanswered", "answered_no"):
                status, evidence_parts = "answered_ok", [text_snip]
            elif status == "answered_alt":
                evidence_parts.append(text_snip)
            break
    # alt 证据合并：保留最后 3 段（含新时间的在后面）
    evidence = " / ".join(evidence_parts[-3:]) if evidence_parts else None
    return {"reply_status": status, "reply_evidence": evidence}


def enrich_events_with_resolution(events: List[Dict[str, Any]],
                                  messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """批量：给每个事件附加 reply_status / reply_evidence。

    messages 必须按 sent_at 升序、含全部相关群消息。
    """
    # 按群分组消息（保持时间序）
    by_group: Dict[str, List[Dict[str, Any]]] = {}
    for m in messages:
        by_group.setdefault(m.get("conversation_name") or "", []).append(m)

    for ev in events:
        gm = by_group.get(ev.get("conversation_name") or "", [])
        res = analyze_resolution(ev, gm)
        ev["reply_status"] = res["reply_status"]
        ev["reply_evidence"] = res["reply_evidence"]
    return events
