"""导出文件适配器注册与解析路由。

支持：
- MessageHub 标准 JSONL / JSON（含完整字段）
- Moect 微信导出（JSON / JSONL，启发式字段映射，待真实样本校验）
- 通用 CSV / TXT（每行一条文本）

所有解析器返回统一 RawMessage 字典，字段对齐 DB schema：
  source_platform, source_method, account_ref, conversation_id,
  conversation_name, sender_name, sent_at, text, message_type,
  source_message_id, raw_ref, time_confidence, capture_status
"""
from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

CN_TZ = timezone(timedelta(hours=8))


def _to_iso(ts_raw, prefer_tz: str = "+08:00") -> tuple[str, str]:
    """尽力把各种时间表示转成 ISO8601+08:00。返回 (iso_str, confidence)。"""
    if ts_raw is None or ts_raw == "":
        return "", "unknown"
    # 数字：秒或毫秒 epoch
    if isinstance(ts_raw, (int, float)):
        try:
            if ts_raw > 1e12:  # 毫秒
                dt = datetime.fromtimestamp(ts_raw / 1000, tz=CN_TZ)
            else:  # 秒
                dt = datetime.fromtimestamp(ts_raw, tz=CN_TZ)
            return dt.isoformat(), "exact"
        except (ValueError, OSError):
            return "", "unknown"
    s = str(ts_raw).strip()
    # 已是 ISO
    if "T" in s or (s.count("-") >= 2 and " " not in s and "T" not in s):
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=CN_TZ)
            return dt.isoformat(), "exact"
        except ValueError:
            pass
    # 常见 "YYYY-MM-DD HH:MM:SS" 或 "YYYY/MM/DD HH:MM:SS"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt).replace(tzinfo=CN_TZ)
            return dt.isoformat(), "exact"
        except ValueError:
            continue
    return "", "unknown"


def _norm_text(v) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return v.get("text") or v.get("content") or json.dumps(v, ensure_ascii=False)
    return str(v)


# ------------------------- 解析器 -------------------------

def parse_standard(obj: dict) -> Optional[dict]:
    """MessageHub 标准格式（字段齐全）。"""
    text = _norm_text(obj.get("text"))
    if not text and not obj.get("conversation_name"):
        return None
    iso, conf = _to_iso(obj.get("sent_at") or obj.get("timestamp"))
    return {
        "source_platform": obj.get("source_platform") or "unknown",
        "source_method": obj.get("source_method") or "file_import",
        "account_ref": obj.get("account_ref"),
        "conversation_id": obj.get("conversation_id") or obj.get("conversation_name") or "unknown",
        "conversation_name": obj.get("conversation_name") or obj.get("conversation_id") or "unknown",
        "sender_name": obj.get("sender_name") or "unknown",
        "sent_at": iso or obj.get("sent_at", ""),
        "text": text,
        "message_type": obj.get("message_type") or "text",
        "source_message_id": obj.get("source_message_id") or obj.get("id"),
        "time_confidence": conf,
        "capture_status": obj.get("capture_status") or "full",
    }


def parse_moect(obj: dict, fallback_platform: str = "wechat") -> Optional[dict]:
    """Moect 微信导出 JSON/JSONL 启发式解析。

    Moect 官方未公开固定 schema，此处按常见字段名猜测：
      - 时间: time / sendTime / timestamp / msgTime / createTime
      - 会话: talker / roomName / conversationName / chatName / groupName / name
      - 发送者: sender / senderName / from / speaker / nickname
      - 文本: content / text / msg / message
    真实样本到位后据此校准；解析失败返回 None。
    """
    text = _norm_text(obj.get("text") or obj.get("content") or obj.get("msg") or obj.get("message"))
    if not text:
        return None
    conv = (obj.get("conversationName") or obj.get("chatName") or obj.get("roomName")
            or obj.get("talker") or obj.get("groupName") or obj.get("name") or "Moect会话")
    sender = (obj.get("senderName") or obj.get("sender") or obj.get("from")
              or obj.get("speaker") or obj.get("nickname") or "unknown")
    iso, conf = _to_iso(obj.get("sendTime") or obj.get("sent_at") or obj.get("timestamp")
                        or obj.get("msgTime") or obj.get("createTime") or obj.get("time"))
    return {
        "source_platform": obj.get("source_platform") or fallback_platform,
        "source_method": "wechat_moect_export",
        "account_ref": obj.get("account_ref"),
        "conversation_id": obj.get("conversation_id") or conv,
        "conversation_name": conv,
        "sender_name": sender,
        "sent_at": iso,
        "text": text,
        "message_type": obj.get("message_type") or "text",
        "source_message_id": obj.get("source_message_id") or obj.get("id") or obj.get("localId"),
        "time_confidence": conf,
        "capture_status": "full",
    }


def parse_weflow_json(data: dict) -> list[dict]:
    """WeFlow CLI `json` 格式：{weflow, session, messages}。

    字段名按 weflow 桌面版一致（wiki）推断，宽容映射：
      session.username / display_name → 会话
      messages[].sender/from/talker, CreateTime/create_time/time,
      content/msg/text, MsgSvrID/server_id/local_id/id, type_name/local_type
    待真实样本校准。
    """
    out = []
    session = data.get("session") or {}
    conv_name = (session.get("display_name") or session.get("nickname")
                 or session.get("username") or session.get("name") or "WeChat会话")
    conv_id = session.get("username") or session.get("id") or conv_name
    msgs = data.get("messages") or []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        text = _norm_text(m.get("content") or m.get("msg") or m.get("text") or m.get("src"))
        if not text:
            continue
        sender = (m.get("sender") or m.get("from") or m.get("talker")
                  or m.get("sender_name") or m.get("nickname") or "unknown")
        iso, conf = _to_iso(m.get("CreateTime") or m.get("create_time")
                            or m.get("createTime") or m.get("time"))
        sid = (m.get("MsgSvrID") or m.get("server_id") or m.get("serverId")
               or m.get("local_id") or m.get("id"))
        return_dict = {
            "source_platform": "wechat",
            "source_method": "weflow_export",
            "account_ref": session.get("owner") or session.get("self"),
            "conversation_id": conv_id,
            "conversation_name": conv_name,
            "sender_name": sender,
            "sent_at": iso,
            "text": text,
            "message_type": str(m.get("type_name") or m.get("local_type") or "text"),
            "source_message_id": str(sid) if sid is not None else None,
            "time_confidence": conf,
            "capture_status": "full",
        }
        out.append(return_dict)
    return out


def parse_weflow_chatlab_jsonl(lines: list[str]) -> list[dict]:
    """WeFlow `chatlab-jsonl`：每行一个对象，`_type` 依次为 header/member/消息。

    待真实样本校准。message 对象宽容映射 sender/time/content/id。
    """
    out = []
    conv_name = "WeChat会话"
    conv_id = conv_name
    members = {}  # member_id -> name
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        t = obj.get("_type") or obj.get("type")
        if t == "header":
            meta = obj.get("meta") or obj
            conv_name = (meta.get("name") or meta.get("display_name")
                         or meta.get("session_name") or conv_name)
            conv_id = meta.get("session_id") or meta.get("username") or conv_name
        elif t == "member":
            mid = obj.get("id") or obj.get("wxid") or obj.get("username")
            mname = obj.get("name") or obj.get("display_name") or obj.get("nickname")
            if mid and mname:
                members[mid] = mname
        else:
            # 消息行（_type 为 text/image/voice/... 或无 _type）
            text = _norm_text(obj.get("content") or obj.get("text") or obj.get("msg"))
            if not text:
                continue
            sender_ref = obj.get("sender") or obj.get("from") or obj.get("sender_id")
            sender = members.get(sender_ref) or sender_ref or obj.get("sender_name") or "unknown"
            iso, conf = _to_iso(obj.get("time") or obj.get("timestamp") or obj.get("CreateTime"))
            sid = obj.get("id") or obj.get("server_id") or obj.get("MsgSvrID") or obj.get("local_id")
            out.append({
                "source_platform": "wechat",
                "source_method": "weflow_export",
                "account_ref": None,
                "conversation_id": conv_id,
                "conversation_name": conv_name,
                "sender_name": sender,
                "sent_at": iso,
                "text": text,
                "message_type": str(t or "text"),
                "source_message_id": str(sid) if sid is not None else None,
                "time_confidence": conf,
                "capture_status": "full",
            })
    return out


def parse_csv(path: str) -> list[dict]:
    """通用 CSV：尝试识别列名（模糊包含匹配），否则按 群/发送者/时间/内容 顺序。"""
    out = []
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames:
            lowered = {fn: fn.lower() for fn in reader.fieldnames}

            def col(*keywords):
                for kw in keywords:
                    for fn, low in lowered.items():
                        if kw in low:
                            return fn
                return None

            c_conv = col("conversation_name", "group", "群", "chat", "会话", "conversation")
            c_sender = col("sender_name", "sender", "发送者", "from", "speaker", "nickname", "昵称")
            c_time = col("sent_at", "timestamp", "time", "时间", "日期", "datetime")
            c_text = col("text", "content", "message", "内容", "消息", "msg")
            for row in reader:
                text = _norm_text(row.get(c_text) if c_text else None)
                if not text:
                    continue
                iso, conf = _to_iso(row.get(c_time) if c_time else None)
                out.append({
                    "source_platform": "manual",
                    "source_method": "csv_import",
                    "conversation_id": row.get(c_conv) or "csv",
                    "conversation_name": row.get(c_conv) or "CSV导入",
                    "sender_name": row.get(c_sender) or "unknown",
                    "sent_at": iso,
                    "text": text,
                    "message_type": "text",
                    "source_message_id": None,
                    "time_confidence": conf,
                    "capture_status": "full",
                })
        else:
            # 无表头：按顺序 群,发送者,时间,内容
            f.seek(0)
            reader = csv.reader(f)
            for row in reader:
                if len(row) < 4:
                    continue
                iso, conf = _to_iso(row[2])
                out.append({
                    "source_platform": "manual",
                    "source_method": "csv_import",
                    "conversation_id": row[0],
                    "conversation_name": row[0],
                    "sender_name": row[1],
                    "sent_at": iso,
                    "text": row[3],
                    "message_type": "text",
                    "source_message_id": None,
                    "time_confidence": conf,
                    "capture_status": "full",
                })
    return out


def parse_txt(path: str, platform: str = "manual") -> list[dict]:
    """通用 TXT：每行一条文本，统一归入一个会话。"""
    out = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    for i, line in enumerate(lines):
        out.append({
            "source_platform": platform,
            "source_method": "txt_import",
            "conversation_id": os.path.basename(path),
            "conversation_name": os.path.basename(path),
            "sender_name": "unknown",
            "sent_at": "",
            "text": line,
            "message_type": "text",
            "source_message_id": None,
            "time_confidence": "unknown",
            "capture_status": "partial",
        })
    return out


# ------------------------- 路由 -------------------------

def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _try_json(content: str):
    """content → dict/list/None（不抛异常）。"""
    try:
        return json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return None


def _nonempty_lines(content: str) -> list[str]:
    return [ln for ln in content.splitlines() if ln.strip()]


def _first_json_line(content: str):
    """非空行里找到的第一个合法 JSON 对象（JSONL 头探测用）。"""
    for ln in _nonempty_lines(content):
        obj = _try_json(ln)
        if isinstance(obj, dict):
            return obj
    return None


def _parse_jsonl_lines(content: str) -> list[dict]:
    """逐行 parse_standard → parse_moect 兜底。"""
    out = []
    for line in _nonempty_lines(content):
        obj = _try_json(line)
        if obj is None:
            continue
        r = parse_standard(obj) or parse_moect(obj)
        if r:
            out.append(r)
    return out


def _parse_moect_formal_lines(content: str) -> list[dict]:
    """逐行 Moect 正式格式解析（metadata 头自动跳过）。"""
    out = []
    for line in _nonempty_lines(content):
        obj = _try_json(line)
        if obj is None:
            continue
        parsed = parse_moect_formal(obj)
        if parsed:
            out.append(parsed)
    return out


def _parse_plain_json_items(top) -> list[dict]:
    """单个对象或对象数组：parse_standard → parse_moect 兜底。"""
    items = top if isinstance(top, list) else [top]
    out = []
    for item in items:
        if isinstance(item, dict):
            r = parse_standard(item) or parse_moect(item)
            if r:
                out.append(r)
    return out


def _route_json(content: str, ext: str) -> tuple[list[dict], str]:
    """.json/.jsonl 内容 → (消息列表, 平台提示)。

    有序规则表驱动，顺序即优先级。设计约束（均来自实测教训）：
    1. 单对象 JSON（WeFlow）顶层也常有 metadata，必须先于
       「按行逐个解析」的 JSONL 格式判定，否则被误判为 Moect 行格式且返回空。
    2. 探测命中但解析为空（损坏文件）→ 落到下一规则，最终返回空列表而非报错。
    """
    top = _try_json(content)          # 仅当整体是单个 JSON 时非 None
    first = _first_json_line(content)  # 每行格式的头部对象

    # ---- 单对象 JSON（只可能出现在 .json；.jsonl 整体 parse 必失败 → top=None）----
    if isinstance(top, dict) and "messages" in top:
        if "session" in top or "weflow" in top:
            return parse_weflow_json(top), "wechat"

    # ---- 按行格式 ----
    if isinstance(first, dict):
        if "_type" in first:
            return parse_weflow_chatlab_jsonl(_nonempty_lines(content)), "wechat"
        if "metadata" in first or "server_id" in first:
            return _parse_moect_formal_lines(content), "wechat"
        if ext == ".jsonl":
            return _parse_jsonl_lines(content), "unknown"

    # ---- 兜底：单 JSON 数组/对象，或 .json 逐条 ----
    if top is not None:
        return _parse_plain_json_items(top), "unknown"
    if ext == ".json":
        # .json 但整体非法：尝试按行救回（与旧行为一致，尽力而为）
        return _parse_jsonl_lines(content), "unknown"
    return [], "unknown"


def detect_and_parse(path: str) -> tuple[list[dict], str]:
    """根据文件内容自动路由到解析器。返回 (raw_messages, platform_hint)。

    路由逻辑见 _route_json（有序规则，顺序即优先级）。
    platform_hint 用于无法从内容推断平台时（如纯 TXT）的默认平台提示。
    """
    ext = os.path.splitext(path)[1].lower()

    if ext == ".csv":
        return parse_csv(path), "unknown"

    if ext == ".txt":
        return parse_txt(path), "manual"

    if ext in (".json", ".jsonl"):
        try:
            return _route_json(_read_text(path), ext)
        except (json.JSONDecodeError, ValueError):
            # 单行内容合法但整体损坏时已由 _try_json 兜住；此处防御意外抛出
            return [], "unknown"

    return [], "unknown"


def parse_moect_formal(obj: dict) -> Optional[dict]:
    """Moect 正式格式解析（含 metadata 头文件和详细消息字段）。

    支持字段：
      - metadata.session.name / session.username → 会话名
      - messages[].sender / sender_wxid / sender_name → 发送者
      - time_str / create_time → 时间
      - text / content → 文本
      - local_id / server_id → 消息 ID
      - type_name → 消息类型
    """
    # 跳过 metadata 头
    if obj.get("metadata"):
        return None
    
    text = _norm_text(obj.get("text") or obj.get("content") or "")
    if not text and obj.get("type") not in (1, 47):  # 允许纯表情
        return None
    
    # 时间处理：优先 time_str，其次 convert create_time
    time_str = obj.get("time_str")
    create_time = obj.get("create_time")
    
    if time_str:
        # "2025-06-30 14:48:33" → ISO
        try:
            dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=CN_TZ)
            iso_ts = dt.isoformat()
            conf = "exact"
        except ValueError:
            iso_ts, conf = "", "unknown"
    elif create_time:
        iso_ts, conf = _to_iso(create_time)
    else:
        iso_ts, conf = "", "unknown"
    
    sender = obj.get("sender") or obj.get("sender_wxid") or obj.get("from") or "unknown"
    conv_name = obj.get("session") or obj.get("conversationName") or "WeChat 会话"
    conv_id = obj.get("session") or obj.get("conversationId") or conv_name
    
    message_type = str(obj.get("type_name") or obj.get("kind") or "text")
    source_id = str(obj.get("server_id") or obj.get("local_id") or "")
    
    return {
        "source_platform": "wechat",
        "source_method": "wechat_moect_formal",
        "account_ref": None,
        "conversation_id": conv_id.split("@")[0] if "@" in conv_id else conv_id,  # 提取 wxid
        "conversation_name": conv_name,
        "sender_name": sender,
        "sent_at": iso_ts,
        "text": text,
        "message_type": message_type,
        "source_message_id": source_id if source_id else None,
        "time_confidence": conf,
        "capture_status": "full",
    }
