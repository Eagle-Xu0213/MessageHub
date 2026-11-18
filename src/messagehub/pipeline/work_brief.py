"""工作信息整理引擎：把真实聊天记录整理成结构化「工作简报」。

设计原则（与交接书 §5 一致，但修正旧规则引擎的失败模式）：
1. 不逐条消息建任务 —— 21 万条真实闲聊会产生上千条假任务。
   改为「先筛工作相关候选 → 再按语义归类」的聚合式整理。
2. 每条结论必须挂原文证据 ID，且 ID 必须真实存在于候选集中，防止 AI 编造。
3. AI 不可用时降级为本地规则简报，永远有输出，绝不假装「无工作信息」。
4. 无时间戳消息单独统计、默认排除，不混入按日简报，边界写清楚。

输出结构：
{
  scope, stats, overview, sections: {todos, notices, replies, decisions, unclear},
  evidence: {id: {...}}, candidates: [...], degraded, source_note, generated_at
}
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from messagehub.config import DB_PATH as _CONFIG_DB_PATH, friendly_name

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = _CONFIG_DB_PATH
CACHE_DIR = PROJECT_ROOT / "reports"

SECTION_KEYS = ("todos", "notices", "replies", "decisions", "unclear")
SECTION_META = {
    "todos": ("待办与截止", "#d9381e", "需要行动的事项、责任人、截止时间"),
    "notices": ("通知与安排", "#1668dc", "会议、活动、时间地点、值班排班"),
    "replies": ("需回复 / 需确认", "#d48806", "点名回复、接龙、确认类消息"),
    "decisions": ("决策与变更", "#389e0d", "已定事项、方案调整、经费与报销"),
    "unclear": ("待确认 / 信息不全", "#8c8c8c", "疑似工作信息但缺少责任或时间"),
}


# ------------------------- 候选筛选 -------------------------

_SIGNALS: Dict[str, str] = {
    "todos": (
        r"请[^，。]{0,14}(?:提交|完成|填写|报名|回复|统计|汇总|发送|整理|准备|确认|上报|落实|核对|打印|盖章|签字|收集|发给|发到|转发)"
        r"|务必|尽快|抓紧|记得(?:交|发|填|提交|报名)"
        r"|需要(?:提交|完成|填写|准备|上报|汇总)"
        r"|截止|截至|ddl|DDL|deadline|之前(?:交|发|提交|完成|上报)|前(?:交|发|提交|完成|上报)"
        r"|待办|任务|分工|负责|牵头|跟进|落实|对接|催"
    ),
    "notices": (
        r"通知|安排|会议|开会|例会|活动|讲座|培训|值班|彩排|团建|答辩|面试|评审|选举|报名"
        r"|地点|集合|几点|出发|时间(?:定|改|是|安排)|日程|流程|议程"
        r"|周[一二三四五六日天]|\d{1,2}月\d{1,2}[日号]|\d{1,2}[:：]\d{2}|上午|下午|晚上"
    ),
    "replies": (
        r"收到请回复|请回复|回复一下|回复[我1]|确认一下|请确认|接龙|报名接龙"
        r"|@[^\s]{1,16}|是否可以|可以吗|行不行|有没有问题|麻烦确认"
    ),
    "decisions": (
        r"决定|定了|定在|改为|改成|取消|推迟|延期|提前|通过|审批|同意|不同意"
        r"|报销|经费|预算|采购|下单|策划案|方案|最终|不再|调整为|结果是|结论"
    ),
    "org": (
        r"团委|学院|学校|办公室|辅导员|书记|部长|主任|副主任|社团|志愿服务|志愿公益"
        r"|党支部|书院|学生处|教务处|老师|同学|主席|负责人|各部门|组织|中心"
    ),
}
_SIGNAL_RES = {k: re.compile(v) for k, v in _SIGNALS.items()}

_NOISE_RES = [
    re.compile(r"^\s*$"),
    re.compile(r"^\[[^\]]{1,24}\]\s*$"),                      # [动画表情]、[图片]
    re.compile(r"^[\W\d_]+$", re.UNICODE),                    # 纯符号/数字
    re.compile(r"^<sysmsg", re.I),                            # 系统 XML
    re.compile(r"^(?:收到|好的|好|嗯+|哈哈+|呵+|ok|OK|okk|了解|明白|谢谢|辛苦了|可以|行|对|是的|没问题|啊|哦|诶|哈|哈哈|嘿嘿|emm|6+|666|确实|真的|笑死|离谱)[\s\W！!。.，,~]*$", re.I),
    re.compile(r"^https?://\S+$"),
]

_AT = re.compile(r"@([^\s@，,。:：/\[\]]{1,16})")
_DEADLINE = re.compile(
    r"(?:\d{1,2}月\d{1,2}[日号](?:[^\s，。]{0,10})?)"
    r"|(?:(?:今|明|后|大后)天(?:上午|下午|晚上|中午)?(?:\d{1,2}[点时:：]\d{0,2})?)"
    r"|(?:下?周[一二三四五六日天](?:前|之前)?(?:\s*(?:上午|下午|晚上))?(?:\d{1,2}[点时:：]\d{0,2})?)"
    r"|(?:\d{1,2}[:：]\d{2})"
    r"|(?:截止|截至|deadline|ddl)[:：]?\s*[^\s，。;；]{0,20}"
)
_SHARE = re.compile(r"^\[(?:链接|视频|视频号|小程序|文件|图片|语音|动画表情|表情)\]")


def analyze_message(text: str) -> Tuple[float, List[str], bool]:
    """返回 (工作相关分数, 命中分类, 是否为分享类低置信消息)。"""
    t = (text or "").strip()
    if not t or len(t) < 6:
        return 0.0, [], False
    for p in _NOISE_RES:
        if p.match(t):
            return 0.0, [], False

    tags = [k for k in ("todos", "notices", "replies", "decisions")
            if _SIGNAL_RES[k].search(t)]
    score = 0.0
    score += 1.4 * len(tags)
    if _SIGNAL_RES["org"].search(t):
        score += 0.8
    if _AT.search(t):
        score += 0.5
    if len(t) >= 30:
        score += 0.5
    if len(t) >= 80:
        score += 0.4
    is_share = bool(_SHARE.match(t))
    if is_share:
        score -= 1.2
    return score, tags, is_share


def is_work_related(text: str, threshold: float = 1.4) -> bool:
    score, _, _ = analyze_message(text)
    return score >= threshold


# ------------------------- 数据加载 -------------------------

def _rows_to_messages(rows) -> List[Dict[str, Any]]:
    out = []
    for r in rows:
        m = dict(r)
        cid = str(m.get("conversation_id") or "")
        m["conversation_name"] = friendly_name(cid, str(m.get("conversation_name") or ""))
        m["text"] = str(m.get("text") or "").strip()
        m["is_undated"] = not str(m.get("sent_at") or "").startswith("20")
        out.append(m)
    return out


def available_dates(db_path: str = None, limit: int = 400) -> List[Dict[str, Any]]:
    """列出有时间戳的消息按日统计（倒序）。"""
    conn = sqlite3.connect(str(db_path or DB_PATH))
    try:
        rows = conn.execute(
            "SELECT substr(sent_at,1,10) d, COUNT(*) n, COUNT(DISTINCT sender_name) s "
            "FROM messages WHERE sent_at LIKE '20%' GROUP BY d ORDER BY d DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [{"date": r[0], "messages": r[1], "senders": r[2]} for r in rows]
    finally:
        conn.close()


def available_groups(db_path: str = None) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(str(db_path or DB_PATH))
    try:
        rows = conn.execute(
            "SELECT conversation_id, conversation_name, COUNT(*) n FROM messages "
            "GROUP BY conversation_id ORDER BY n DESC"
        ).fetchall()
        return [
            {
                "conversation_id": r[0],
                "conversation_name": friendly_name(str(r[0] or ""), str(r[1] or "")),
                "messages": r[2],
            }
            for r in rows
        ]
    finally:
        conn.close()


def data_quality(db_path: str = None) -> Dict[str, Any]:
    """明确标注数据完整性，避免把缺失时间伪装成「无工作信息」。"""
    conn = sqlite3.connect(str(db_path or DB_PATH))
    try:
        total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        undated = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE sent_at IS NULL OR sent_at NOT LIKE '20%'"
        ).fetchone()[0]
        no_sender = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE sender_name IN ('','unknown') OR sender_name IS NULL"
        ).fetchone()[0]
        return {
            "total_messages": total,
            "dated_messages": total - undated,
            "undated_messages": undated,
            "unknown_sender_messages": no_sender,
            "note": "无时间戳消息默认不进入按日简报，可在「全库检索」模式中查看",
        }
    finally:
        conn.close()


def load_messages(
    date: Optional[str] = None,
    days: int = 1,
    conversation_id: Optional[str] = None,
    keyword: Optional[str] = None,
    include_undated: bool = False,
    all_dated: bool = False,
    db_path: str = None,
    max_rows: int = 20000,
) -> Dict[str, Any]:
    """按范围加载原始消息。

    - date + days：以 date 为结束日，往前 days 天
    - all_dated / keyword：跨全库检索（keyword 模式可含无时间戳消息）
    - 返回 {messages, raw_count, undated_excluded, scope_label}
    """
    conn = sqlite3.connect(str(db_path or DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        where, params = [], []

        if keyword:
            where.append("text LIKE ?")
            params.append(f"%{keyword}%")
            if not include_undated:
                where.append("sent_at LIKE '20%'")
            scope_label = f"全库关键词「{keyword}」"
        elif all_dated:
            where.append("sent_at LIKE '20%'")
            scope_label = "全部有日期消息"
        else:
            if not date:
                row = conn.execute(
                    "SELECT substr(sent_at,1,10) FROM messages WHERE sent_at LIKE '20%' "
                    "ORDER BY sent_at DESC LIMIT 1"
                ).fetchone()
                date = row[0] if row else datetime.now().strftime("%Y-%m-%d")
            end = datetime.strptime(date, "%Y-%m-%d").date()
            start = end - timedelta(days=max(1, days) - 1)
            where.append("sent_at >= ? AND sent_at < ?")
            params += [f"{start.isoformat()}T00:00:00+08:00",
                       f"{(end + timedelta(days=1)).isoformat()}T00:00:00+08:00"]
            scope_label = date if days == 1 else f"{start.isoformat()} ~ {end.isoformat()}（{days} 天）"

        if conversation_id:
            where.append("conversation_id = ?")
            params.append(conversation_id)

        sql = "SELECT * FROM messages WHERE " + " AND ".join(where) + " ORDER BY sent_at ASC, id ASC"
        rows = _rows_to_messages(conn.execute(sql, params).fetchall())

        undated_excluded = 0
        if not keyword and not include_undated:
            # 统计同范围内被排除的无时间戳消息（用于页面明示）
            undated_excluded = sum(1 for m in rows if m["is_undated"])

        raw = rows[:max_rows]
        return {
            "messages": raw,
            "raw_count": len(rows),
            "truncated": len(rows) > max_rows,
            "undated_excluded": undated_excluded,
            "scope_label": scope_label,
            "date": date,
            "days": days,
        }
    finally:
        conn.close()


def select_candidates(messages: List[Dict[str, Any]], threshold: float = 1.4,
                      limit: int = 120) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """筛出工作相关候选，附带打分与命中标签。"""
    scored = []
    for m in messages:
        score, tags, is_share = analyze_message(m["text"])
        if score < threshold:
            continue
        c = dict(m)
        c["score"] = round(score, 2)
        c["tags"] = tags
        c["is_share"] = is_share
        if _DEADLINE.search(c["text"]):
            c["deadline_hint"] = _DEADLINE.search(c["text"]).group(0)
        else:
            c["deadline_hint"] = None
        at = _AT.search(c["text"])
        c["mention"] = at.group(1) if at else None
        scored.append(c)

    scored.sort(key=lambda x: (-x["score"], str(x.get("sent_at") or ""), x["id"]))
    kept = scored[:limit]
    kept.sort(key=lambda x: (str(x.get("sent_at") or "9999"), x["id"]))
    return kept, {"scored": len(scored), "kept": len(kept), "truncated": len(scored) > limit}


# ------------------------- 本地规则简报（降级保底） -------------------------

def _rule_title(text: str) -> str:
    clean = re.sub(r"^\[[^\]]{1,20}\]\s*", "", text).strip()
    clean = re.sub(r"\s+", " ", clean)
    for sent in re.split(r"[。！？!?\n]+", clean):
        if len(sent.strip()) >= 6:
            return sent.strip()[:48]
    return clean[:48] or "（无正文）"


def rule_brief(candidates: List[Dict[str, Any]], scope: Dict[str, Any],
               stats: Dict[str, Any]) -> Dict[str, Any]:
    """不依赖任何外部服务的结构化整理，按信号强弱归类。"""
    sections: Dict[str, List[Dict[str, Any]]] = {k: [] for k in SECTION_KEYS}
    for c in candidates:
        tags = c.get("tags") or []
        if "todos" in tags:
            key = "todos"
        elif "replies" in tags:
            key = "replies"
        elif "decisions" in tags:
            key = "decisions"
        elif "notices" in tags:
            key = "notices"
        else:
            key = "unclear"
        # 只有泛组织词、无任何动作/安排信号 → 待确认
        if not tags:
            key = "unclear"
        item = {
            "title": _rule_title(c["text"]),
            "detail": c["text"][:240],
            "owner": c.get("mention"),
            "deadline": c.get("deadline_hint"),
            "evidence_ids": [c["id"]],
            "source": "rule",
            "time": (c.get("sent_at") or "")[11:16] or "时间未知",
        }
        sections[key].append(item)

    total_items = sum(len(v) for v in sections.values())
    overview = (
        f"{scope['scope_label']} 共 {stats['total_messages']} 条消息，"
        f"筛出 {len(candidates)} 条工作相关候选，归为 {total_items} 个条目。"
        "本简报由本地规则生成（AI 服务未启用或暂不可用），条目按原文信号强弱分类，"
        "请结合下方原文判断是否需要行动。"
    )
    return {
        "scope": scope,
        "stats": stats,
        "overview": overview,
        "sections": sections,
        "degraded": True,
        "source_note": "本地规则整理（未调用 AI）· 每条均可追溯原文",
    }


# ------------------------- AI 结构化整理 -------------------------

def _call_llm_json(prompt: str, timeout: float = 60.0) -> Optional[dict]:
    """委托 LLMProvider.chat_json（统一降级链），本模块不再自建副本。"""
    from messagehub.llm.provider import LLMProvider
    return LLMProvider.from_env().chat_json(prompt, timeout=timeout)


def _build_prompt(candidates: List[Dict[str, Any]], scope: Dict[str, Any]) -> str:
    lines = []
    for c in candidates:
        t = (c.get("sent_at") or "")[11:16] or "时间未知"
        sender = c.get("sender_name") or "未知"
        group = c.get("conversation_name") or "未标注"
        text = re.sub(r"\s+", " ", c["text"])[:220]
        lines.append(f"[{c['id']}] {t} {group}/{sender}: {text}")
    joined = "\n".join(lines)

    return (
        "你是工作信息整理助手。下面是某个工作群在指定范围内、经规则初筛的聊天消息。\n"
        "请把它整理成一份**可执行的中文工作简报**。\n\n"
        f"范围：{scope['scope_label']}\n"
        f"候选消息（共 {len(candidates)} 条，方括号内是证据编号）：\n{joined}\n\n"
        "严格返回 JSON 对象，不要 markdown 代码块、不要解释文字：\n"
        '{"overview":"两三句话说明这个范围内的工作重点",'
        '"todos":[{"title":"待办事项","detail":"具体要做什么","owner":"负责人或null",'
        '"deadline":"截止时间或null","evidence_ids":[123,456]}],'
        '"notices":[{"title":"通知/安排","detail":"时间地点与内容","evidence_ids":[123]}],'
        '"replies":[{"title":"需回复或需确认的事","detail":"说明","evidence_ids":[123]}],'
        '"decisions":[{"title":"已定/已变更事项","detail":"说明","evidence_ids":[123]}],'
        '"unclear":[{"title":"信息不全、需要人工确认的事","detail":"缺什么信息","evidence_ids":[123]}]}\n\n'
        "硬性要求：\n"
        "1. evidence_ids 只能填上面出现过的真实编号，且必须与条目内容对应；每条至少 1 个。\n"
        "2. 只依据原文，严禁编造人名、时间、事件、金额。原文没写就填 null 或留空数组。\n"
        "3. 闲聊、表情、纯调侃、无行动含义的内容不要放进任何分类，直接忽略。\n"
        "4. 同一件事被多人重复提及，合并为一条，evidence_ids 列出所有相关编号。\n"
        "5. 五个分类彼此互斥；没有内容的分类返回空数组。\n"
        "6. 全部输出使用简体中文。"
    )


def _validate_ai(data: dict, candidate_ids: set) -> Dict[str, List[Dict[str, Any]]]:
    """校验 AI 输出：丢弃编造的证据编号，无证据的条目降级到 unclear。"""
    sections: Dict[str, List[Dict[str, Any]]] = {k: [] for k in SECTION_KEYS}
    for key in SECTION_KEYS:
        raw_items = data.get(key) or []
        if not isinstance(raw_items, list):
            continue
        for it in raw_items:
            if not isinstance(it, dict):
                continue
            ids = []
            for v in (it.get("evidence_ids") or []):
                try:
                    n = int(v)
                except (TypeError, ValueError):
                    continue
                if n in candidate_ids and n not in ids:
                    ids.append(n)
            if not ids:
                continue
            sections[key].append({
                "title": str(it.get("title") or "（无标题）")[:80],
                "detail": str(it.get("detail") or "")[:400],
                "owner": it.get("owner") or None,
                "deadline": it.get("deadline") or None,
                "evidence_ids": ids,
                "source": "ai",
                "time": "见原文",
            })
    return sections


def build_brief(
    date: Optional[str] = None,
    days: int = 1,
    conversation_id: Optional[str] = None,
    keyword: Optional[str] = None,
    include_undated: bool = False,
    all_dated: bool = False,
    threshold: float = 1.4,
    use_ai: bool = True,
    cache: bool = True,
    db_path: str = None,
) -> Dict[str, Any]:
    """生成完整工作简报（含证据映射、候选原文、缓存）。"""
    loaded = load_messages(
        date=date, days=days, conversation_id=conversation_id, keyword=keyword,
        include_undated=include_undated, all_dated=all_dated, db_path=db_path,
    )
    messages = loaded["messages"]
    candidates, sel_info = select_candidates(messages, threshold=threshold)

    cn_tz = timezone(timedelta(hours=8))
    scope = {
        "date": loaded["date"],
        "days": loaded["days"],
        "scope_label": loaded["scope_label"],
        "conversation_id": conversation_id,
        "keyword": keyword,
        "include_undated": include_undated,
    }
    senders = Counter(str(m.get("sender_name") or "未知") for m in messages)
    groups = Counter(m["conversation_name"] for m in messages)
    stats = {
        "total_messages": loaded["raw_count"],
        "work_candidates": len(candidates),
        "candidate_pool": sel_info["scored"],
        "candidates_truncated": sel_info["truncated"],
        "active_senders": len(senders),
        "groups": [{"name": k, "messages": v} for k, v in groups.most_common(10)],
        "top_senders": [{"name": k, "messages": v} for k, v in senders.most_common(8)],
        "undated_excluded": loaded["undated_excluded"],
        "raw_truncated": loaded["truncated"],
        "data_quality": data_quality(db_path),
    }

    # 缓存键
    key_src = json.dumps(
        {k: scope[k] for k in ("date", "days", "scope_label", "conversation_id", "keyword", "include_undated")}
        | {"threshold": threshold, "use_ai": use_ai, "all_dated": all_dated},
        ensure_ascii=False, sort_keys=True,
    )
    cache_key = hashlib.sha256(key_src.encode("utf-8")).hexdigest()[:12]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"work_brief_{cache_key}.json"
    if cache and cache_file.exists():
        try:
            saved = json.loads(cache_file.read_text(encoding="utf-8"))
            saved["cached"] = True
            return saved
        except Exception:
            pass

    fallback = rule_brief(candidates, scope, stats)
    result = fallback
    if use_ai and candidates:
        candidate_ids = {int(c["id"]) for c in candidates}
        ai = _call_llm_json(_build_prompt(candidates, scope))
        if ai:
            sections = _validate_ai(ai, candidate_ids)
            if any(sections.values()):
                result = {
                    "scope": scope,
                    "stats": stats,
                    "overview": str(ai.get("overview") or fallback["overview"])[:600],
                    "sections": sections,
                    "degraded": False,
                    "source_note": "AI 结构化整理（免费模型）· 每条均校验过原文证据编号",
                }
            else:
                result = dict(fallback)
                result["source_note"] = "AI 返回的证据编号均无效，已回退本地规则整理"
        else:
            result = dict(fallback)
            result["source_note"] = "AI 服务暂不可用，已回退本地规则整理"

    evidence = {}
    for c in candidates:
        evidence[str(c["id"])] = {
            "id": c["id"],
            "sender": c.get("sender_name") or "未知",
            "time": c.get("sent_at") or "时间未知",
            "group": c.get("conversation_name") or "未标注",
            "text": c["text"],
            "undated": c["is_undated"],
        }
    result["evidence"] = evidence
    result["candidates"] = [
        {"id": c["id"], "sender": c.get("sender_name") or "未知",
         "time": c.get("sent_at") or "时间未知", "group": c["conversation_name"],
         "text": c["text"], "score": c["score"], "tags": c["tags"],
         "undated": c["is_undated"]}
        for c in candidates
    ]
    result["generated_at"] = datetime.now(cn_tz).strftime("%Y-%m-%d %H:%M:%S")
    result["cached"] = False
    result["section_meta"] = {k: {"label": v[0], "color": v[1], "hint": v[2]}
                              for k, v in SECTION_META.items()}

    try:
        cache_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[work_brief] 缓存写入失败：{e}")
    return result
