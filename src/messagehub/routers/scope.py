"""检测范围管理路由：/select 页面与会话清单/分级保存/AI 预选 API。

职责（2026-11-18 用户新需求）：
1. 读取本地解密后的微信联系人/群聊清单（Moect 快照 contact.db + session.db），
   合并本库 messages 表的统计（消息量、最近活跃）。
2. 用户按三级分类勾选：工作 / 混合 / 不检测。
   - 群聊 → personal.json 的 groups.work / groups.watch / groups.excluded
   - 联系人 → personal.json 的 contacts.work / contacts.watch / contacts.excluded（新增段）
3. AI 自动分级（预勾选）：仅用 .env 配置的主 LLM 端点（本地/自建优先），
   不走 provider 的公网免费降级链——会话名单与消息统计是隐私元数据，
   不应发给不可控的公共服务。LLM 不可用时回退本地启发式。
4. 保存后调用 personal.reload() 让规则引擎热更新，无需重启服务。

安全边界：清单数据只在本机内存中组装，保存只写 config/personal.json
（gitignore）；LLM 提示词只含统计特征与脱敏样本，不含完整聊天正文。
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import anyio
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel

from messagehub.config import BASE_DIR, CN_TZ, DB_PATH, TEMPLATES_DIR
from messagehub import personal
from messagehub.database import get_db
from messagehub.routers import qq

router = APIRouter()

# 快照目录（与采集桥同一来源；采集桥负责刷新与合并，这里只读）
MOECT_DATA_DIR = BASE_DIR / "runtime" / "moect_data"
SNAPSHOT_CURRENT = MOECT_DATA_DIR / "archives" / "current"
# AppData 只读回退（未跑过采集桥时可能只有这里）
LOCALAPPDATA_SNAP = Path.home() / "AppData" / "Local" / "Moect" / "archives"

# 列表返回上限：contact 全表 1.6 万行，前端只需要最近活跃的会话。
MAX_CONVERSATIONS = 500

# 系统账号/公众号过滤（不出现在勾选清单里）
_SYSTEM_PREFIXES = ("gh_", "brandsessionholder", "notifymessage", "weixin",
                    "filehelper", "fmessage", "medianote", "floatbottle")
# 4.x 常规联系人 local_type 取值集合；集合外的保守跳过
_KNOWN_LOCAL_TYPES = (0, 1, 2, 4, 8, 256, 2048)


def _find_snapshot() -> Optional[Path]:
    """定位可用快照：项目 current 优先，其次 AppData 下最新一份。"""
    contact = SNAPSHOT_CURRENT / "contact" / "contact.db"
    if contact.is_file():
        return SNAPSHOT_CURRENT
    try:
        bases = [p for p in LOCALAPPDATA_SNAP.glob("*/snapshot_*") if p.is_dir()]
        if bases:
            return max(bases, key=lambda p: p.stat().st_mtime)
    except OSError:
        pass
    return None


def _ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    # 聚合查询（GROUP BY/ORDER BY）会落 SQLite 临时文件；某些环境
    # （沙箱/只读目录）tempdir 不可写导致 "unable to open database file"。
    # 会话统计的分组基数很小（几百个会话），放内存毫无压力。
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def _recent_activity_from_messages() -> Dict[str, Dict[str, Any]]:
    """本库 messages 表的会话统计：总消息数 / 最近消息时间。

    群与私聊统一按 conversation_name 聚合（采集桥写入时两者同源：
    群=群名，私聊=联系人备注/昵称）。
    """
    out: Dict[str, Dict[str, Any]] = {}
    if not DB_PATH.exists():
        return out
    try:
        conn = _ro(DB_PATH)
        rows = conn.execute(
            "SELECT conversation_name, COUNT(*) AS cnt, MAX(sent_at) AS last_at "
            "FROM messages WHERE source_platform != 'qq' GROUP BY conversation_name").fetchall()
        conn.close()
        for r in rows:
            out[r["conversation_name"]] = {
                "msg_count": r["cnt"],
                "last_at": r["last_at"],
            }
    except sqlite3.Error:
        pass
    return out


def _load_conversations() -> List[Dict[str, Any]]:
    """从快照组装会话清单（群 + 联系人），按最近活跃排序。

    - 群：contact.username 以 @chatroom 结尾
    - 联系人：非 @chatroom、非公众号/系统账号
    - 活跃度优先级：session.db 最近时间 > messages 表统计 > 沉底
    """
    snap = _find_snapshot()
    if not snap:
        return []

    contact_db = snap / "contact" / "contact.db"
    if not contact_db.is_file():
        return []

    session_ts: Dict[str, int] = {}
    sess_db = snap / "session" / "session.db"
    if sess_db.is_file():
        try:
            conn = _ro(sess_db)
            for r in conn.execute(
                    "SELECT username, sort_timestamp FROM SessionTable"):
                session_ts[r["username"]] = int(r["sort_timestamp"] or 0)
            conn.close()
        except sqlite3.Error:
            pass

    items: List[Dict[str, Any]] = []
    conn = _ro(contact_db)
    try:
        rows = conn.execute(
            "SELECT username, nick_name, remark, local_type, delete_flag "
            "FROM contact").fetchall()
    except sqlite3.Error:
        conn.close()
        return []
    conn.close()

    for r in rows:
        u = (r["username"] or "").strip()
        if not u or r["delete_flag"]:
            continue
        is_room = u.endswith("@chatroom")
        if not is_room:
            if u.startswith(_SYSTEM_PREFIXES):
                continue
            if r["local_type"] not in _KNOWN_LOCAL_TYPES:
                continue
        name = (r["remark"] or "").strip() or (r["nick_name"] or "").strip()
        if not name or name == "-":
            continue
        items.append({
            "id": u,
            "name": name,
            "kind": "group" if is_room else "contact",
            "_ts": session_ts.get(u, 0),
        })

    act = _recent_activity_from_messages()
    now = datetime.now(CN_TZ).timestamp()
    for it in items:
        a = act.get(it["name"], {})
        it["msg_count"] = a.get("msg_count", 0)
        it["last_at"] = a.get("last_at")
        ts = it.pop("_ts")
        if not ts and it["last_at"]:
            try:
                ts = int(datetime.fromisoformat(it["last_at"]).timestamp())
            except (ValueError, TypeError):
                ts = 0
        it["_rank"] = ts or 0
        it["active_recent"] = bool(ts and now - ts < 30 * 86400)  # 30 天内活跃
    items.sort(key=lambda x: x.pop("_rank"), reverse=True)

    # 采集桥能产出的会话才有意义；contact 表里有大量从未聊过的僵尸联系人
    kept = [it for it in items if it["active_recent"] or it["msg_count"] > 0]
    return kept[:MAX_CONVERSATIONS]


def _current_levels() -> Dict[str, Dict[str, List[str]]]:
    """当前分级（来自 personal.json 运行时值），前端渲染勾选态。"""
    return {
        "groups": {
            "work": sorted(personal.WORK_GROUPS),
            "watch": sorted(personal.WATCH_GROUPS),
            "excluded": sorted(personal.EXCLUDED_GROUPS),
        },
        "contacts": {
            "work": sorted(personal.WORK_CONTACTS),
            "watch": sorted(personal.WATCH_CONTACTS),
            "excluded": sorted(personal.EXCLUDED_CONTACTS),
        },
    }


def _save_config(groups: Optional[Dict[str, List[str]]],
                 contacts: Optional[Dict[str, List[str]]],
                 group_ids: Optional[Dict[str, Dict[str, str]]] = None,
                 qq_levels: Optional[Dict[str, str]] = None) -> None:
    """把提交的平台分级写回 config/personal.json。

    其他段（identity/aliases/brief）原样保留；写前先备份一份 .bak。
    """
    path = personal.active_config_path()
    try:
        data = personal.read_local_config()
    except (OSError, ValueError):
        raise HTTPException(500, "无法读取个人配置，请检查文件格式和权限") from None
    previous = {key: data.get(key) for key in ("groups", "contacts", "group_ids")}
    if path.exists():
        path.with_suffix(".json.bak").write_text(
            path.read_text(encoding="utf-8"), encoding="utf-8")

    def _clean(lst: Any) -> List[str]:
        if not isinstance(lst, list):
            return []
        seen, out = set(), []
        for v in lst:
            s = str(v).strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
        return out

    if groups is not None and contacts is not None:
        data["groups"] = {
            "work": _clean(groups.get("work")),
            "watch": _clean(groups.get("watch")),
            "excluded": _clean(groups.get("excluded")),
        }
        # Save stable IDs only when a display name maps unambiguously to one group.
        bindings = group_ids if group_ids is not None else (data.get('group_ids') or {})
        data['group_ids'] = {level: {gid: name for gid, name in (bindings.get(level) if isinstance(bindings.get(level), dict) else {}).items()
                                           if isinstance(gid, str) and gid.endswith('@chatroom') and name in data['groups'][level]}
                             for level in ('work', 'watch', 'excluded')}
        data["contacts"] = {
            "work": _clean(contacts.get("work")),
            "watch": _clean(contacts.get("watch")),
            "excluded": _clean(contacts.get("excluded")),
        }
    if qq_levels is not None:
        prior = data.get("qq_scope") or {}
        known = {row["id"]: row for row in _qq_conversations()}
        data["qq_scope"] = {
            key: value for key, value in prior.items() if key not in known
        } | {
            row["id"]: {
                "level": qq_levels[row["id"]],
                "since": (prior.get(row["id"]) or {}).get("since") or datetime.now(CN_TZ).isoformat(),
                "baseline_id": (prior.get(row["id"]) or {}).get("baseline_id", row["latest_id"]),
            }
            for row in known.values()
            if qq_levels.get(row["id"]) in ("work", "watch")
        }
    data.setdefault("identity", {})

    if any(data.get(key) != value for key, value in previous.items()):
        data["scope_updated_at"] = datetime.now(CN_TZ).isoformat()
    personal.write_local_config(data)


@router.get("/select", response_class=HTMLResponse)
async def select_page():
    """检测范围管理页面"""
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    return HTMLResponse(env.get_template("select.html").render())


def _qq_conversations() -> list[dict]:
    conn = get_db()
    try:
        rows = conn.execute("""SELECT m.id, m.conversation_id, m.conversation_name, latest.last_received
            FROM messages m JOIN (
                SELECT MAX(id) AS id,
                       MAX(CAST(strftime('%s', sent_at) AS INTEGER)) AS last_received
                FROM messages
                WHERE source_platform='qq' AND source_method IN ('qq_chat_exporter_v5', 'qq_onebot')
                  AND (conversation_id LIKE 'qq:group:%' OR conversation_id LIKE 'qq:contact:%')
                GROUP BY conversation_id
            ) latest ON latest.id=m.id ORDER BY m.id DESC""")
        scope = personal._CONFIG.get("qq_scope") or {}
        items = []
        for row in rows:
            _, kind, identity = row["conversation_id"].split(":", 2)
            try:
                account, peer = json.loads(identity)
                if not isinstance(account, str) or not isinstance(peer, str) or not account or not peer:
                    continue
            except (TypeError, ValueError):
                continue
            items.append({
                "id": row["conversation_id"], "name": row["conversation_name"], "platform": "qq",
                "kind": kind, "identity_hint": f"{account[-4:]} / {peer[-4:]}",
                "latest_id": row["id"], "level": (scope.get(row["conversation_id"]) or {}).get("level"),
                "last_received": max(row["last_received"] or 0,
                                     qq.catalog.get(row["conversation_id"], {}).get("last_received", 0)),
            })
        existing = {row["id"] for row in items}
        items.extend(
            {**row, "latest_id": 0, "level": (scope.get(key) or {}).get("level")}
            for key, row in qq.catalog.items() if key not in existing
        )
        local_activity = qq.local_group_activity()
        for row in items:
            row["last_received"] = max(row.get("last_received", 0), local_activity.get(row["id"], 0))
        items.sort(key=lambda row: row["last_received"], reverse=True)
        return items
    finally:
        conn.close()


@router.get("/api/scope/conversations")
async def scope_conversations():
    """会话清单 + 当前分级。"""
    items = _load_conversations()
    return JSONResponse({
        "conversations": items + _qq_conversations(),
        "levels": _current_levels(),
        "snapshot_available": _find_snapshot() is not None,
        "group_ids": personal.group_bindings(),
    })


class ScopeSave(BaseModel):
    groups: Optional[Dict[str, List[str]]] = None
    contacts: Optional[Dict[str, List[str]]] = None
    group_ids: Optional[Dict[str, Dict[str, str]]] = None
    qq_levels: Optional[Dict[str, str]] = None


@router.post("/api/scope/config")
async def save_scope(payload: ScopeSave):
    """保存分级配置（前端提交勾选结果）。"""
    wechat = payload.groups is not None and payload.contacts is not None
    if (payload.groups is None) != (payload.contacts is None) or (payload.group_ids is not None and not wechat):
        raise HTTPException(422, "微信分级需要同时提交群聊和联系人")
    if not wechat and payload.qq_levels is None:
        raise HTTPException(422, "没有提交检测范围")
    _save_config(payload.groups, payload.contacts, payload.group_ids, payload.qq_levels)
    return JSONResponse({
        "status": "ok",
        "levels": _current_levels(),
        "note": "规则引擎已热更新；实时采集器下一轮生效。",
    })


# ==================== AI 自动分级 ====================

# 任务信号词（本地启发式与 LLM 提示词共用）
WORK_KEYWORDS = ("截止", "提交", "会议", "通知", "安排", "值班", "材料",
                 "表格", "报告", "ddl", "deadline", "盖章", "审核",
                 "签到", "报名", "周报", "汇总", "对接", "跟进", "任务",
                 "填写", "收集", "问卷", "接龙", "评审", "答辩", "老师")


def _conversation_digest(name: str, kind: str,
                         max_msgs: int = 60) -> Optional[Dict[str, Any]]:
    """抽取单个会话的近期特征摘要（供分级判断）。

    只提取统计特征与脱敏样本（截断 40 字），不把完整正文打包上传。
    """
    if not DB_PATH.exists():
        return None
    try:
        conn = _ro(DB_PATH)
        rows = conn.execute(
            "SELECT sender_name, text, sent_at FROM messages "
            "WHERE conversation_name = ? ORDER BY sent_at DESC LIMIT ?",
            (name, max_msgs)).fetchall()
        conn.close()
    except sqlite3.Error:
        return None
    if not rows:
        return None

    my_names = personal.MY_SENDER_NAMES | {personal.MY_REALNAME}
    n_total = len(rows)
    n_me = sum(1 for r in rows
               if (r["sender_name"] or "").strip() in my_names)
    texts = [(r["sender_name"] or "", (r["text"] or "")[:40]) for r in rows]

    kw_hits: Counter = Counter()
    for _, t in texts:
        tl = t.lower()
        for kw in WORK_KEYWORDS:
            if kw in tl:
                kw_hits[kw] += 1

    # 脱敏样本：发送方向 + 截断正文，去重取 12 条
    samples, seen = [], set()
    for who, t in texts:
        if not t or t in seen:
            continue
        seen.add(t)
        direction = "我" if who in my_names else "对方"
        samples.append(f"{direction}: {t}")
        if len(samples) >= 12:
            break

    span_days = None
    try:
        span_days = max(1, (datetime.now(CN_TZ)
                            - datetime.fromisoformat(rows[-1]["sent_at"])).days)
    except (ValueError, TypeError):
        pass

    return {
        "name": name,
        "kind": kind,
        "recent_messages": n_total,
        "my_ratio": round(n_me / max(1, n_total), 2),
        "work_keyword_hits": dict(kw_hits.most_common(6)),
        "samples": samples,
        "span_days": span_days,
    }


def _heuristic_level(digest: Dict[str, Any]) -> tuple:
    """本地启发式分级（LLM 不可用时的兜底）。

    返回 (level, score0to1, reason)。
    """
    kind = digest["kind"]
    hits = sum(digest["work_keyword_hits"].values())
    my_ratio = digest["my_ratio"]

    if kind == "contact":
        # 私聊：对方主动推消息（我方占比低）+ 任务词密集 → 大概率工作
        if hits >= 3 and my_ratio <= 0.4:
            return "work", min(0.9, 0.5 + hits * 0.08), \
                f"近期 {digest['recent_messages']} 条中任务词命中 {hits} 次，对方主动发消息为主"
        if hits >= 1:
            return "watch", 0.55, f"出现少量任务词（{hits} 次），可能工作生活混合"
        return "excluded", 0.7, "近期无任务信号，多为日常交流"
    # 群聊
    if hits >= 8:
        return "work", min(0.92, 0.5 + hits * 0.05), \
            f"任务词命中 {hits} 次，工作信号密集"
    if hits >= 2:
        return "watch", 0.6, f"少量任务词（{hits} 次），可能工作生活混合"
    return "excluded", 0.65, "无明显工作信号"


@router.get("/api/scope/llm-status")
async def llm_status():
    """主 LLM 端点可用性（只看 .env 主端点，不探测公网降级链）。"""
    from messagehub.llm.provider import LLMProvider
    llm = LLMProvider.from_env()
    public_hints = ("pollinations", "llm7", "api.openai.com")
    is_public = any(h in (llm.base_url or "") for h in public_hints)
    return JSONResponse({
        "configured": llm.enabled,
        "base_url": llm.base_url,
        "model": llm.model,
        "uses_public_service": bool(llm.enabled and is_public),
    })


class PreselRequest(BaseModel):
    names: List[Dict[str, str]]  # [{"name": ..., "kind": "group|contact"}]


def _build_presel_prompt(digests: Dict[str, Dict[str, Any]]) -> str:
    """组装 AI 分级提示词（统计特征 + 脱敏样本，不含完整正文）。"""
    blocks = []
    for d in digests.values():
        kws = ", ".join(f"{k}×{v}"
                        for k, v in d["work_keyword_hits"].items()) or "无"
        samples = "\n".join("    " + s for s in d["samples"][:8]) or "    （无文本样本）"
        span = f"，时间跨度 {d['span_days']} 天" if d["span_days"] else ""
        blocks.append(
            f"[{d['kind']}] {d['name']}\n"
            f"  近期消息 {d['recent_messages']} 条，我方占比 {d['my_ratio']}{span}\n"
            f"  任务词命中: {kws}\n"
            f"  样本:\n{samples}")
    names_line = ", ".join(d["name"] for d in digests.values())
    return (
        "你是微信消息分类助手。下面是若干会话（群聊 group / 私聊 contact）的"
        "近期消息统计特征与脱敏样本。请把每个会话分为三级：\n"
        "- work：内容基本都是工作（通知/任务/材料/会议等）\n"
        "- watch：工作与生活混合\n"
        "- excluded：几乎纯生活闲聊\n"
        "判定要点：私聊里对方主动发消息且带任务词 → work 倾向；群聊任务词密集"
        " → work；全是表情包/闲聊/日常问候 → excluded。\n\n"
        "会话清单：" + names_line + "\n\n" +
        "\n\n".join(blocks) +
        '\n\n输出 JSON：{"levels": [{"name": "会话名", "level": "work|watch|excluded", '
        '"score": 0到1的置信度, "reason": "一句话中文理由"}]}，'
        "levels 数组必须覆盖清单中的每个会话，只输出 JSON。"
    )


async def ai_presel(payload: PreselRequest):
    """AI 自动分级（预勾选）。

    策略：本地启发式打底（全部会话都有结果）→ .env 主端点 LLM 对
    「本库有消息的会话」精修。LLM 失败/未配置时返回纯启发式结果。
    """
    results: Dict[str, Dict[str, Any]] = {}
    digests: Dict[str, Dict[str, Any]] = {}

    for ref in payload.names:
        name = (ref.get("name") or "").strip()
        kind = ref.get("kind") or "group"
        if not name or name in results:
            continue
        digest = _conversation_digest(name, kind)
        if digest is None:
            results[name] = {"level": None, "score": 0,
                             "reason": "本库没有该会话的消息，无法判断",
                             "source": "none"}
            continue
        digests[name] = digest
        level, score, reason = _heuristic_level(digest)
        results[name] = {"level": level, "score": score,
                         "reason": reason, "source": "heuristic"}

    llm_source = "heuristic"
    if digests:
        from messagehub.llm.provider import LLMProvider
        llm = LLMProvider.from_env()
        if llm.enabled:
            # 必须放线程池：chat_json(timeout=90) 同步跑在 async 里会
            # 卡死事件循环，拖垮全服务（2026-11-19 事故，规则同 brief.py）
            prompt = _build_presel_prompt(digests)
            data = await anyio.to_thread.run_sync(
                lambda: llm.chat_json(prompt, timeout=90, retries=1))
            if data and isinstance(data.get("levels"), list):
                for it in data["levels"]:
                    n = (it.get("name") or "").strip()
                    lv = it.get("level")
                    if n in digests and lv in ("work", "watch", "excluded"):
                        results[n] = {
                            "level": lv,
                            "score": min(1.0, max(0.0, float(it.get("score", 0.6)))),
                            "reason": (it.get("reason") or "").strip()[:120]
                                      or results[n]["reason"],
                            "source": "llm",
                        }
                        llm_source = "llm"

    return JSONResponse({
        "levels": results,
        "source": llm_source,
        "analyzed": len(digests),
        "total": len(results),
    })
