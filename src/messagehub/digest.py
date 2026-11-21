"""唤醒补扫 + 加急分流引擎。

设计目标（用户 2026-11-20 定）：
- 早上开电脑时间不固定，离开期间积压的消息不该靠"几点发一封"来兜，
  而应在程序唤醒时把「上次整理水位 → 现在」这一段的事件一次性整理成邮件。
- 真正急的事（直呼 + 指派 + 48h 内截止）不能等唤醒，扫到就立即加急发信，
  并发一条 Windows 桌面通知。
- 非紧急事件由 LLM 打语义标签（课程作业/会议安排/报名截止…），按标签分组，
  而不是按消息时间流水账。

水位与去重：
- runtime/digest_state.json 存 last_digest_at（整理水位）、urgent_sent_ids
  （已加急的 message_id，防同一条反复发）。
- 与 brief_baseline.json 完全独立：那条基线服务网页简报，本文件服务邮件整理。
- 所有 LLM 调用失败都降级：标签退化为群名，截止解析退化为规则；
  任何一环失败都不阻断主流程。
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from messagehub.config import BASE_DIR, CN_TZ, DB_PATH, now_cn_iso
from messagehub.llm.provider import LLMProvider
from messagehub import personal
from messagehub.rules import extract_events
from messagehub.resolution import enrich_events_with_resolution

# 状态文件跟随数据库目录：生产库 → runtime/digest_state.json；
# 测试库 → runtime/pytest_db/digest_state.json（自动隔离，绝不污染生产水位）。
STATE_FILE = DB_PATH.parent / "digest_state.json"

# 已闭环状态：这类事件不需要提醒，但保留在整理邮件末尾供回顾
RESOLVED_STATES = {"answered_ok", "answered_alt", "answered_no"}

# 唤醒补扫默认回溯窗口（首次运行、无水位时）：3 天，避免一口气整理几个月
DEFAULT_LOOKBACK_DAYS = 3
# 加急检查窗口：只看最近 3 天的事件，防止翻旧账
URGENT_LOOKBACK_DAYS = 3
# 加急的截止阈值：48 小时内
URGENT_DEADLINE_HOURS = 48


# ---------------- 水位状态 ----------------

def _empty_state() -> Dict[str, Any]:
    return {
        "last_digest_at": None,
        "urgent_sent_ids": [],
        "last_digest_run": None,
        "last_urgent_run": None,
    }


def load_state() -> Dict[str, Any]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return _empty_state()
    except (OSError, ValueError):
        return _empty_state()
    base = _empty_state()
    base.update(data)
    # 去重列表有上限，防止无限膨胀（保留最近 500 条）
    ids = base.get("urgent_sent_ids")
    base["urgent_sent_ids"] = ids[-500:] if isinstance(ids, list) else []
    return base


def save_state(state: Dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------- 事件收集 ----------------

def _load_messages_since(since: Optional[str], days: int) -> List[Dict[str, Any]]:
    """读水位之后的消息。上下文窗口用更长的一段（不截断提取质量）。"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        if since:
            rows = conn.execute(
                "SELECT id, sent_at, conversation_name, sender_name, text "
                "FROM messages WHERE sent_at LIKE '20%' AND sent_at > ? "
                "ORDER BY sent_at ASC, id ASC", (since,)).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, sent_at, conversation_name, sender_name, text "
                "FROM messages WHERE sent_at LIKE '20%' "
                "ORDER BY sent_at ASC, id ASC").fetchall()
    finally:
        conn.close()
    msgs = [dict(r) for r in rows]
    if not since and days and days > 0 and msgs:
        # 无水位时按最新消息往前推 N 天（不是按今天，适配历史库）
        latest = max((m.get("sent_at") or "")[:10] for m in msgs)
        try:
            anchor = datetime.fromisoformat(latest)
        except ValueError:
            anchor = datetime.now(CN_TZ)
        cutoff = (anchor - timedelta(days=days - 1)).isoformat()
        msgs = [m for m in msgs if (m.get("sent_at") or "")[:10] >= cutoff]
    return msgs


def collect_events_since(since: Optional[str],
                         days: int = DEFAULT_LOOKBACK_DAYS,
                         llm: Optional[LLMProvider] = None
                         ) -> List[Dict[str, Any]]:
    """提取水位之后的工作事件，并附带闭环状态。

    闭环分析用「水位前 30 天」的消息做上下文，避免刚跨水位的事件被误判为未答复。
    llm 传入且可用时，对规则判 unanswered 的事件追加 LLM 语义复核（R1.2）；
    llm 为 None（默认）时行为与旧版完全一致，测试/离线路径不受影响。
    """
    msgs = _load_messages_since(since, days)
    if not msgs:
        return []
    # 上下文：取全库中该时间段前后一部分（简单起见用同批消息；跨水位的回复
    # 由 enrich 的窗口决定，通常 15 条内，本批已足够覆盖绝大多数场景）
    events = extract_events(msgs)
    events = enrich_events_with_resolution(events, msgs)
    if llm and llm.enabled:
        events = llm_verify_resolution(events, msgs, llm)
    return events


# ---------------- LLM 闭环复核 ----------------

# 单事件复核时给 LLM 看的后续消息条数上限（防止长流水账稀释语义）
_VERIFY_CONTEXT_MESSAGES = 20


def _reply_context(event: Dict[str, Any],
                   messages: List[Dict[str, Any]],
                   limit: int = _VERIFY_CONTEXT_MESSAGES) -> str:
    """拼事件之后的群消息（你的发言标注"我"），供 LLM 判断是否已办结。"""
    ev_id = event.get("message_id")
    my = set(personal.MY_SENDER_NAMES)
    lines = []
    started = False
    for m in messages:
        if not started:
            if m.get("id") == ev_id:
                started = True
            continue
        who = "我" if (m.get("sender_name") or "").strip() in my else (m.get("sender_name") or "?")
        text = (m.get("text") or "").strip()
        if text:
            lines.append(f"{who}: {text[:100]}")
        if len(lines) >= limit:
            break
    return "\n".join(lines)


def llm_verify_resolution(events: List[Dict[str, Any]],
                          messages: List[Dict[str, Any]],
                          llm: Optional[LLMProvider] = None) -> List[Dict[str, Any]]:
    """对规则判为 unanswered 的事件做 LLM 批量语义复核（R1.2）。

    词表闭环只认口头禅（好的/已经发了），覆盖不了「人家要文件、
    你直接把文件发过去（[文件] 占位符之前的旧消息）」「你回复了实质
    内容但没说收到」这类语义层面的办结。这里把事件原文+后续上下文
    交给 LLM 判断："这件事是否已经不需要用户再做什么"。

    - 只复核 unanswered 的事件（其他状态信任规则结果）
    - 判为 done → reply_status=answered_ok，evidence 记 LLM 依据
    - LLM 失败/解析失败 → 原样保留 unanswered（宁可多提醒，不装已办）
    - 修改的是入参列表，返回同一引用，调用方无感
    """
    pending = [e for e in events if e.get("reply_status") == "unanswered"]
    if not pending or not (llm and llm.enabled):
        return events

    my = set(personal.MY_SENDER_NAMES)
    lines = []
    ctx_by_id: Dict[int, str] = {}
    for i, ev in enumerate(pending):
        ctx = _reply_context(ev, messages)
        ctx_by_id[i] = ctx
        lines.append(
            f"事件{i}: [{ev.get('conversation_name')}] "
            f"{ev.get('sender_name')}: {(ev.get('text') or '')[:120]}\n"
            f"之后的群消息:\n{ctx or '(无)'}")
    prompt = (
        "下面是若干条工作事件和事件后的群聊消息。判断每条事件是否已经办结——"
        "即用户自己已完成交付（如已把文件发到群里）、已明确答复、或事情已被别人"
        "接手/取消，用户不再需要做任何事。\n"
        "注意：事件后别人追问/催促 = 未办结；[文件][图片] 是占位符，"
        "看它出现在谁的消息里。\n"
        "严格返回 JSON：{\"results\": [true/false, ...]}，数组顺序与事件编号一致。\n"
        + "\n\n".join(lines)
    )
    data = llm.chat_json(prompt, timeout=60.0)
    results = data.get("results") if data else None
    if not isinstance(results, list) or len(results) != len(pending):
        # 解析失败 → 保持原状（unanswered），宁可多提醒
        return events

    by_msg_id = {m.get("id"): m for m in messages}
    for i, ev in enumerate(pending):
        if results[i] is True:
            ev["reply_status"] = "answered_ok"
            ev["reply_evidence"] = "LLM 判定已办结"
    # 回填消息数，日志可见
    return events


# ---------------- 截止时间解析 ----------------

_DEADLINE_RULE = re.compile(
    r"(?:今天|今晚|明天|明晚|后天|大后天|下?周[一二三四五六日天]|"
    r"\d{1,2}[月/-]\d{1,2}[日号]?|\d{1,2}[:：]\d{2}|"
    r"截止|截至|ddl|DDL|deadline|最迟|最晚|之前|前交|前发|前给|以内)")


def parse_deadline(event: Dict[str, Any],
                   llm: Optional[LLMProvider] = None) -> Optional[datetime]:
    """把 deadline 原文解析成具体时间。LLM 优先，规则降级。

    返回 None 表示"无法判定为临近截止"，加急判定会因此不触发（保守）。
    """
    raw = (event.get("deadline") or "").strip()
    text = (event.get("text") or "").strip()
    sent_at = event.get("sent_at") or now_cn_iso()
    if not raw and not text:
        return None

    if llm and llm.enabled:
        prompt = (
            "从聊天消息里判断截止时间。严格返回 JSON："
            '{"deadline_iso": "2026-11-22T18:00:00+08:00" 或 null}。\n'
            "规则：只填写能明确判断的日期时间；'明天下午三点'要换算成具体时刻；"
            "完全无法确定就填 null；不要编造。\n"
            f"消息时间：{sent_at}\n截止原文：{raw or '无'}\n消息全文：{text[:300]}\n"
            "只返回 JSON。"
        )
        data = llm.chat_json(prompt, timeout=20.0)
        if data:
            iso = data.get("deadline_iso")
            if isinstance(iso, str) and iso:
                try:
                    parsed = datetime.fromisoformat(iso)
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=CN_TZ)
                    return parsed
                except ValueError:
                    pass
            # 模型明确返回 null = 判定无截止，不再用规则兜（避免误报）
            return None

    # 规则降级：只处理能高置信推断的"今天/今晚/明天"，其余不算临近截止
    if not _DEADLINE_RULE.search(raw or text):
        return None
    try:
        base = datetime.fromisoformat(sent_at)
        if base.tzinfo is None:
            base = base.replace(tzinfo=CN_TZ)
    except ValueError:
        base = datetime.now(CN_TZ)
    blob = raw + " " + text
    if re.search(r"今天|今晚", blob):
        return base.replace(hour=23, minute=59, second=0, microsecond=0)
    if re.search(r"明天|明晚", blob):
        return (base + timedelta(days=1)).replace(
            hour=23, minute=59, second=0, microsecond=0)
    return None


# ---------------- 加急判定 ----------------

def is_urgent(event: Dict[str, Any],
              llm: Optional[LLMProvider] = None,
              now: Optional[datetime] = None) -> Tuple[bool, Optional[str]]:
    """三者同时满足：直呼/被@ + 强指派 + 48h 内明确截止。

    返回 (是否加急, 原因说明)。任一条件不满足即不加急（宁可漏不可滥）。
    """
    reasons = []
    cue = event.get("cue_type")
    if cue not in ("at_me", "direct_call"):
        return False, f"无直接cue({cue})"
    reasons.append("直呼")

    if (event.get("work_score") or 0) < 2 or event.get("event_type") != "assignment":
        return False, "非强指派"
    reasons.append("指派")

    if event.get("reply_status") in RESOLVED_STATES:
        return False, "已闭环"

    ddl = parse_deadline(event, llm)
    if ddl is None:
        return False, "截止无法判定"
    now = now or datetime.now(CN_TZ)
    delta = ddl - now
    if delta.total_seconds() < -3600:
        return False, "截止已过"
    if delta.total_seconds() > URGENT_DEADLINE_HOURS * 3600:
        return False, f"截止偏远({int(delta.total_seconds()/86400)}天)"
    reasons.append(f"48h内截止")

    return True, " + ".join(reasons)


# ---------------- LLM 语义标签 ----------------

_FALLBACK_TAG_RE = re.compile(r"[\w\u4e00-\u9fff]{2,8}")


def tag_events(events: List[Dict[str, Any]],
               llm: Optional[LLMProvider] = None) -> List[Dict[str, Any]]:
    """给每个事件加 semantic_tag 字段。LLM 批量打标签失败时按群名降级。"""
    if not events:
        return events
    if llm and llm.enabled:
        lines = []
        for i, ev in enumerate(events):
            lines.append(
                f"{i}. [{ev.get('event_type')}] {ev.get('conversation_name')} | "
                f"{(ev.get('text') or '')[:80]}")
        prompt = (
            "给下面每条工作消息打一个简短语义标签（2-6个汉字，如：课程作业、"
            "会议安排、报名截止、值班通知、材料提交、答疑回复）。同类事情用同一个标签。\n"
            "严格返回 JSON：{\"tags\": [\"标签1\", \"标签2\", ...]}，数组长度和顺序必须与输入一致。\n"
            + "\n".join(lines) + "\n只返回 JSON。"
        )
        data = llm.chat_json(prompt, timeout=60.0)
        if data and isinstance(data.get("tags"), list):
            tags = data["tags"]
            if len(tags) == len(events):
                for ev, tag in zip(events, tags):
                    ev["semantic_tag"] = str(tag).strip()[:12] or "其他"
                return events

    # 降级：用群名做标签（同类群自然聚到一起）
    for ev in events:
        ev["semantic_tag"] = (ev.get("conversation_name") or "其他")[:12]
        ev["tag_degraded"] = True
    return events


def group_by_tag(events: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for ev in events:
        groups.setdefault(ev.get("semantic_tag") or "其他", []).append(ev)
    # 按事件数降序，稳定的输出顺序
    return dict(sorted(groups.items(), key=lambda kv: -len(kv[1])))


# ---------------- 主入口 ----------------

def run_catchup(force: bool = False,
                send_mail: bool = True,
                kind: str = "catchup") -> Dict[str, Any]:
    """整理水位之后的所有事件，发一封汇总邮件，推进水位。

    同一个引擎服务两种触发（用户 2026-11-20 最终确认）：
    - kind="catchup"：服务启动时唤醒补扫，整理「上次关机 → 现在」的积压
    - kind="hourly"：常驻期间每小时汇总，整理「上一小时 → 现在」的新增
    两者共用同一个水位（last_digest_at），所以天然不重叠、不漏。

    force=True 忽略"无事件不发"保护（手动调试用）。
    send_mail=False 只生成结果不发信（测试/预览用）。
    """
    state = load_state()
    since = state.get("last_digest_at")
    llm = LLMProvider.from_env()
    events = collect_events_since(since, llm=llm)
    # 已单独加急发过的事件不再塞进汇总，避免同一件事打扰两次
    urgent_ids = {str(x) for x in (state.get("urgent_sent_ids") or [])}
    before_dedup = len(events)
    events = [e for e in events if str(e.get("message_id")) not in urgent_ids]
    deduped_count = before_dedup - len(events)
    open_events = [e for e in events if e.get("reply_status") not in RESOLVED_STATES]
    resolved = [e for e in events if e.get("reply_status") in RESOLVED_STATES]

    result: Dict[str, Any] = {
        "kind": kind,
        "since": since,
        "event_count": len(events),
        "deduped_urgent_count": deduped_count,
        "open_count": len(open_events),
        "resolved_count": len(resolved),
        "sent": False,
        "skipped": None,
    }
    if not open_events and not force:
        result["skipped"] = "无未闭环事件，静默"
        save_state(state)
        return result

    tagged = tag_events(open_events, llm)
    groups = group_by_tag(tagged)

    llm_summary = None
    if llm.enabled and open_events:
        brief_lines = [
            f"[{ev.get('semantic_tag')}] {ev.get('conversation_name')} | "
            f"{(ev.get('text') or '')[:60]}" for ev in tagged[:40]
        ]
        data = llm.chat_json(
            "把下列积压的工作事件整理成一段给本人看的中文摘要，80字以内，"
            "先讲要动手的，再讲通知。不编造。严格返回JSON：{\"summary\":\"...\"}\n"
            + "\n".join(brief_lines), timeout=45.0)
        if data and isinstance(data.get("summary"), str):
            llm_summary = data["summary"].strip()

    result["groups"] = {k: len(v) for k, v in groups.items()}
    result["summary"] = llm_summary
    result["tag_degraded"] = any(e.get("tag_degraded") for e in tagged)

    if send_mail:
        from messagehub import mail
        payload = {
            "kind": kind,  # catchup=开机补扫 / hourly=每小时汇总
            "date": now_cn_iso()[:10],
            "since": since,
            "groups": groups,
            "resolved": resolved,
            "summary": llm_summary,
            "generated_at": now_cn_iso(),
            "stats": {
                "open": len(open_events),
                "resolved": len(resolved),
                "groups": len(groups),
            },
        }
        sent = mail.send_digest_email(payload)
        result["sent"] = sent.get("sent", False)
        result["mail_detail"] = sent.get("detail")

    # 只有发信成功（或 None 测试模式）才推进水位，避免发了失败还丢事件
    if result["sent"] or not send_mail:
        state["last_digest_at"] = now_cn_iso()
        state["last_digest_run"] = now_cn_iso()
        state["last_digest_kind"] = kind
        save_state(state)
    return result


def run_urgent_check(send: bool = True) -> Dict[str, Any]:
    """加急扫描：检查近 3 天的事件，符合条件且未发过的立即加急推送。"""
    state = load_state()
    events = collect_events_since(None, days=URGENT_LOOKBACK_DAYS)
    # 统一按字符串比较：state 里存的是 str，而 message_id 可能是 int，
    # 直接比对会让 99 与 "99" 不相等 → 同一条加急反复发（2026-11-20 实测 bug）。
    sent_ids = {str(x) for x in (state.get("urgent_sent_ids") or [])}
    fresh = [e for e in events if str(e.get("message_id")) not in sent_ids]

    llm = LLMProvider.from_env()
    urgent: List[Dict[str, Any]] = []
    for ev in fresh:
        ok, reason = is_urgent(ev, llm)
        if ok:
            ev["urgent_reason"] = reason
            urgent.append(ev)

    result = {
        "scanned": len(fresh),
        "urgent_count": len(urgent),
        "sent": 0,
        "desktop": 0,
    }
    if not urgent:
        state["last_urgent_run"] = now_cn_iso()
        save_state(state)
        return result

    if send:
        from messagehub import mail, desktop_notify
        for ev in urgent:
            payload = {
                "kind": "urgent",
                "event": ev,
                "generated_at": now_cn_iso(),
            }
            mailed = mail.send_urgent_email(payload)
            if mailed.get("sent"):
                result["sent"] += 1
            try:
                if desktop_notify.notify_urgent(ev):
                    result["desktop"] += 1
            except Exception:
                pass
            sent_ids.add(ev.get("message_id"))

    state["urgent_sent_ids"] = sorted(str(x) for x in sent_ids)[-500:]
    state["last_urgent_run"] = now_cn_iso()
    save_state(state)
    return result
