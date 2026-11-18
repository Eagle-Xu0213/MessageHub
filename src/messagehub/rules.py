"""工作事件提取引擎（R1）：把群消息按已确认规则提取为结构化事件。

规则设计：
1. 群分三层（群名来自 config/personal.json，不在代码里硬编码）：
   - WORK：明确的工作群 → 提取任务指派/@我/通知类事件
   - WATCH：混杂群 → 仅当「cue 我 + 任务语义」双条件满足才报
   - 其余群一律忽略
2. 用户身份 cue 形态（来自 personal.json 的 identity 段）：
   @昵称 / @真名（群昵称可能带班级等前缀）/ 文本直呼短名
3. 事件类型：assignment（指派给我）/ reply_needed（@我提问，需回复）/ notice（时间地点通知，与我个人无关也记）
4. 噪声排除复用 work_brief 的规则：表情包、纯符号、"收到/好的"类应答、玩梗@（与 @12306 并列）

未配置身份或群白名单时，extract_events 直接返回空——不知道「我是谁」
和「哪些是工作群」，任何提取结果都是瞎猜。

输出事件 dict：
{
  message_id, sent_at, conversation_name, sender_name, text,
  event_type,  # assignment | reply_needed | notice
  cue_type,    # at_me | direct_call | context_dialog | none
  deadline,    # 解析出的截止时间原文字串（可为 None）
  work_score,  # 任务语义强度
}
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from messagehub import personal

# ------------------------- 群分层（来自 config/personal.json） -------------------------

WORK_GROUPS = personal.WORK_GROUPS
WATCH_GROUPS = personal.WATCH_GROUPS
EXCLUDED_GROUPS = personal.EXCLUDED_GROUPS

# ------------------------- 用户 cue 形态 -------------------------

# 用户的微信昵称/真名/称呼变体
MY_NICKNAMES = personal.MY_NICKNAMES
MY_REALNAME = personal.MY_REALNAME
MY_SHORTNAME = personal.MY_SHORTNAME


def _build_cue_patterns():
    """按配置动态构建 @我 / 直呼 的正则。

    身份未配置时返回 (None, None)，调用方据此跳过 cue 判定。
    """
    names = [n for n in (MY_NICKNAMES | ({MY_REALNAME} if MY_REALNAME else set())) if n]
    if not names:
        return None, None
    # @提及：允许群昵称带前缀（如 @自动化2401张三），名字后必须是分隔符或结尾
    at_me = re.compile(
        r"@[^\s@，,。:：/\[\]]{0,20}?(?:" + "|".join(map(re.escape, names)) +
        r")(?=[\s，,。:：！!？?\]|]|$)")
    # 直呼：短名后跟任何内容都算（"可以帮我"、人称"你"、语气词都常见）
    # 但排除「世博会/世博园」这类复合词——排除字表来自配置
    call = None
    if MY_SHORTNAME:
        excl = "".join(map(re.escape, sorted(personal.SHORTNAME_EXCLUSIONS)))
        lookahead = f"(?![{excl}])" if excl else ""
        call = re.compile(
            r"(?<![\u4e00-\u9fff])" + re.escape(MY_SHORTNAME) +
            r"(?:老师)?" + lookahead)
    return at_me, call


_AT_ME, _CALL_ME = _build_cue_patterns()

# 玩梗黑名单：@xxx 与 @12306/@10086 并列的整句通常是玩梗
_MEME_AT = re.compile(r"@\s*(?:12306|10086|10010|95588|95533)")

# ------------------------- 任务语义 -------------------------

_ASSIGN = re.compile(
    r"(?:请你?|麻烦你?|辛苦你[一]?个|让你?|需要你|帮忙|帮我把?|帮我拿|协助|安排你?|"
    r"记得(?:交|发|填|提交|报名|回复|处理|准备|拿|取|买|订)|"
    r"(?:提交|完成|填写|核对|打印出来|打印一下|盖章|签字|收集|上报|落实|对接|跟进|值班|带一下|拿一下))"
)
# 祈使句产出物模式：弄个/搞个/写个/做个 + 宾语（策划案/通知/名单…），
# "出来/吧" 结尾的完整祈使（"弄个策划案出来"）。单独编译避免与主 pattern 的括号纠缠。
_ASSIGN_IMPERATIVE = re.compile(r"(?:弄|搞|写|做|出)个[^。！!？?]{1,20}")
# 把字句指派："把 X 发我/交上来/传到群里"（动词在宾语后，词干表匹配不到）
_ASSIGN_BA = re.compile(
    r"把[^。！!？?]{2,30}(?:发|交|传|送|带|拿)(?:给|到|上|好)?(?:我|你|群|老师|同学|主任|负责人|群里|我这里)")
# 二级任务信号：动词宽泛（准备/需要/整理/发送/发给），须与「你/帮/记得/截止」同现才算
_ASSIGN_SOFT = re.compile(
    r"(?:需要你?|准备|整理|发送|发给|订|买|取|拿)(?=[^。！!？?]{0,12}(?:截止|之前|前交|前发|前给|给世博|给老师))"
)
_DEADLINE_HINT = re.compile(
    r"(?:截止|截至|ddl|DDL|deadline|deadline前|之前(?:交|发|提交|完成|上报|回复|给)|"
    r"前(?:交|发|提交|完成|上报|回复|给)|最迟|最晚|以内)"
)
_TIME_HINT = re.compile(
    r"(?:\d{1,2}月\d{1,2}[日号]"
    r"|(?:(?:今|明|后|大后)天)"
    r"|(?:下?下?周[一二三四五六日天])"
    r"|\d{1,2}[:：]\d{2}"
    r"|(?:上午|下午|晚上|中午))"
)
_QUESTION = re.compile(r"[？?]|(?:吗|呢|么)\s*[。.！!~]*$")

_NOISE_RES = [
    re.compile(r"^\s*$"),
    re.compile(r"^\[[^\]]{1,24}\]\s*$"),                      # [动画表情]、[图片]
    re.compile(r"^[\W\d_]+$", re.UNICODE),                    # 纯符号/数字
    re.compile(r"^<sysmsg", re.I),
    re.compile(r"^(?:收到|好的|好|嗯+|哈哈+|呵+|ok|OK|okk|了解|明白|谢谢|辛苦了|可以|行|对|是的|没问题|啊|哦|诶|哈|嘿嘿|emm|6+|666|确实|真的|笑死|离谱|绷不住|绷不住了|强|强强|威武|致敬|崇拜|牛|牛逼|牛b|nb)[\s\W！!。.，,~]*$", re.I),
    re.compile(r"^https?://\S+$"),
]

# 通知类信号：时间地点变更、活动安排、上交材料类指令（工作群里出现即报）
_NOTICE = re.compile(
    r"(?:通知|安排|会议|开会|例会|活动|讲座|培训|值班|彩排|答辩|面试|评审|报名|签到|"
    r"地点|集合|签到码|抽签|材料|问卷|表格|接龙|工资|工时|劳务|"
    r"上传|提交|请各位|@全体成员|请大家)")


def cue_type_of(text: str) -> Optional[str]:
    """判断消息是否在 cue 用户，返回 cue 形态。

    身份未配置时（_AT_ME 为 None）永远返回 None——没有「我」就谈不上 @我。
    """
    if _AT_ME is None:
        return None
    if _MEME_AT.search(text):
        # @12306 这类玩梗句里的 @我 也不报（如"@12306 @我说不允许先生 @10086"）
        if _AT_ME.search(text):
            return None
    if _AT_ME.search(text):
        return "at_me"
    if _CALL_ME is not None and _CALL_ME.search(text):
        return "direct_call"
    return None


def _is_cue_meme(text: str, cue: str) -> bool:
    """混杂群的玩梗排除：cue 我 + 玩梗上下文。

    启发式：同一句里出现多个 @（两个以上）且都是无任务语义的短句。
    """
    if cue is None:
        return True
    ats = re.findall(r"@[^\s@，,。:：/\[\]]{1,20}", text)
    if len(ats) >= 2 and not _ASSIGN.search(text) and not _DEADLINE_HINT.search(text):
        return True  # 如"@12306 @我说不允许先生 @10086竭诚为您服务"
    return False


def work_score_of(text: str) -> int:
    """任务语义强度（0-3）：3=明确指派，2=截止压力，1=弱任务词。"""
    score = 0
    if (_ASSIGN.search(text) or _ASSIGN_IMPERATIVE.search(text)
            or _ASSIGN_BA.search(text)):
        score += 2
    if _ASSIGN_SOFT.search(text):
        score += 1
    if _DEADLINE_HINT.search(text):
        score += 1
    return score


def _extract_deadline(text: str) -> Optional[str]:
    """抽取截止/时间提示（原文字串，不做精确时刻换算）。"""
    m = _DEADLINE_HINT.search(text)
    if m:
        start = max(0, m.start() - 12)
        return text[start:m.end() + 16].strip()
    m = _TIME_HINT.search(text)
    if m:
        start = max(0, m.start() - 6)
        return text[start:m.end() + 12].strip()
    return None


# 用户本人的发送名（闭环检测与上下文指向判定共用，来自 personal.json）
MY_SENDER_NAMES = personal.MY_SENDER_NAMES


def _directed_to_me_heuristic(message: Dict[str, Any],
                              group_messages: List[Dict[str, Any]]) -> bool:
    """上下文指向判定（无直接 cue 的指派句是否发给用户）。

    用户定的规则（2026-11-17）：派活发生在对话流中时不点名——判定方法是
    看这条指派前的小窗口里，发送者与用户是否**正在**互相说话（你来我往）。
    "正在"有双重含义：条数近（前 6 条内）且时间近（对话活跃期 ≤10 分钟）。
    隔了几小时的旧发言不算对话上下文，避免把派给别人的活认成用户的。
    """
    from datetime import datetime

    def _ts(s: str):
        try:
            return datetime.fromisoformat(s)
        except (ValueError, TypeError):
            return None

    ev_idx = None
    for i, m in enumerate(group_messages):
        if m.get("id") == message.get("id"):
            ev_idx = i
            break
    if ev_idx is None:
        return False
    sender = (message.get("sender_name") or "").strip()
    ev_ts = _ts(message.get("sent_at") or "")

    # 指派句之前的 6 条里：发送者与用户都发言过，且都发生在活跃期（≤10 分钟）
    window = group_messages[max(0, ev_idx - 6):ev_idx]
    saw_sender = saw_me = False
    for m in window:
        name = (m.get("sender_name") or "").strip()
        ts = _ts(m.get("sent_at") or "")
        if ev_ts and ts and (ev_ts - ts).total_seconds() > 600:
            continue  # 10 分钟前的发言不算"正在聊"
        if name == sender:
            saw_sender = True
        if name in MY_SENDER_NAMES:
            saw_me = True
    return saw_sender and saw_me


def extract_event(message: Dict[str, Any],
                  group_messages: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """按已确认规则判定单条消息是否为工作事件。

    返回事件 dict 或 None（非事件）。message 需含：
    id, sent_at, conversation_name, sender_name, text。
    group_messages（可选）：同群按时间升序的全部消息，供上下文指向判定。
    """
    group = (message.get("conversation_name") or "").strip()
    text = (message.get("text") or "").strip()

    if not text or group in EXCLUDED_GROUPS:
        return None
    for p in _NOISE_RES:
        if p.match(text):
            return None

    in_work = group in WORK_GROUPS
    in_watch = group in WATCH_GROUPS
    if not in_work and not in_watch:
        return None

    cue = cue_type_of(text)
    score = work_score_of(text)
    is_notice = bool(_NOTICE.search(text))
    # 祈使指派句（弄个/搞个/写个 + 第二人称）——是否指向用户由上下文判定：
    # 有直接 cue → 无条件算；无 cue → 看对话双方是否正在互聊（用户定的规则）
    imperative_assign = bool(_ASSIGN_IMPERATIVE.search(text)
                             and re.search(r"你们?|咱|给我?", text))
    context_directed = False
    if imperative_assign and cue is None and group_messages:
        context_directed = _directed_to_me_heuristic(message, group_messages)

    if in_watch:
        # 混杂群：cue 我 + 任务语义，或（祈使指派 + 上下文指向我）；玩梗排除
        if cue is None and not context_directed:
            return None
        if score < 1:
            return None
        # 玩梗排除只针对有 cue 的句子（无 cue 的祈使指派不走玩梗检查）
        if cue is not None and _is_cue_meme(text, cue):
            return None
        event_type = "assignment" if score >= 2 else "reply_needed"
    else:
        # 工作群：cue 我 → 指派/需回复；无 cue → 有截止或明确通知才报
        if (cue or context_directed) and score >= 2:
            event_type = "assignment"
        elif cue and _QUESTION.search(text):
            event_type = "reply_needed"
        elif (cue or context_directed) and score >= 1:
            event_type = "assignment"
        elif is_notice:
            event_type = "notice"
        else:
            return None

    return {
        "message_id": message.get("id"),
        "sent_at": message.get("sent_at"),
        "conversation_name": group,
        "sender_name": message.get("sender_name"),
        "text": text,
        "event_type": event_type,
        "cue_type": cue or ("context_dialog" if context_directed else "none"),
        "deadline": _extract_deadline(text),
        "work_score": score,
    }


def extract_events(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """批量提取。messages 为 messages 表的 dict 列表（按时间升序）。

    未配置身份或工作群白名单时返回空列表：没有「我」和「工作群」的定义，
    任何事件判定都是无依据的猜测。

    附带「自排除」：发送人是用户本人的消息不产生事件——
    自己发的东西不是给自己的待办（如 Agent2API 介绍、值班取消通知）。
    上下文指向判定需要同群消息窗口，按群分组后逐条传入。
    """
    if not (personal.IDENTITY_CONFIGURED and (WORK_GROUPS or WATCH_GROUPS)):
        return []

    by_group: Dict[str, List[Dict[str, Any]]] = {}
    for m in messages:
        by_group.setdefault(m.get("conversation_name") or "", []).append(m)

    out = []
    for m in messages:
        if (m.get("sender_name") or "").strip() in MY_SENDER_NAMES:
            continue
        ev = extract_event(
            m, group_messages=by_group.get(m.get("conversation_name") or ""))
        if ev:
            out.append(ev)
    return out
