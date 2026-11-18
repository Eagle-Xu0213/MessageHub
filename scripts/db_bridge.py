"""本地聊天数据库 / 导出 → MessageHub 实时桥接（只读上游，仅 POST 到 127.0.0.1:8000）。

背景：微信 4.x 与 QQ NT 的本地数据库均为 SQLCipher 加密，无法直接读。
本脚本不负责解密，只负责把「已解密的数据源」增量接入 MessageHub：

  --mode http    对接本机 chatlog 类 HTTP 服务（默认 http://127.0.0.1:5030/api/v1/chatlog）
  --mode sqlite  只读打开已解密的 SQLite，自动探测消息表并按时间增量
  --mode file    尾随 Moect / 任意 JSONL 导出目录的新增行

用法：
  python scripts/db_bridge.py --mode http --loop --interval 10
  python scripts/db_bridge.py --mode sqlite --db D:/decrypted/message_0.db --loop
  python scripts/db_bridge.py --mode file --dir imports/xxx --once --max-lines 20

所有模式统一：规范化 → 稳定 source_message_id → 批量 POST /api/live/ingest → 水位记录。
不修改任何原始数据库；聊天内容只发往本机 MessageHub，不外传。
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / "runtime" / "db_bridge_state.json"
DEFAULT_INGEST = "http://127.0.0.1:8000/api/live/ingest"
DEFAULT_CHATLOG = "http://127.0.0.1:5030/api/v1/chatlog"
CN_TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def load_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"seen": [], "offsets": {}, "partial": {}, "last_db_time": ""}


def save_state(st: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    st["seen"] = list(dict.fromkeys(st.get("seen", [])))[-20000:]
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def _iso_time(v) -> str:
    """把常见时间格式统一成北京时间 ISO 字符串。"""
    if v is None or v == "":
        return now_iso()
    if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
        try:
            ts = int(v)
            if ts > 1e12:
                ts //= 1000
            return datetime.fromtimestamp(ts, CN_TZ).isoformat(timespec="seconds")
        except Exception:
            return now_iso()
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt).replace(tzinfo=CN_TZ).isoformat()
        except ValueError:
            continue
    return s if "T" in s else now_iso()


def stable_id(m: dict) -> str:
    key = "|".join(str(m.get(k) or "") for k in
                   ("source_platform", "conversation_id", "sender_name", "sent_at", "text"))
    return "db::" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


def normalize(obj: dict, platform: str) -> dict | None:
    if not isinstance(obj, dict):
        return None
    text = str(obj.get("content") or obj.get("text") or obj.get("msg")
               or obj.get("message") or obj.get("str_content") or "").strip()
    if not text:
        return None
    conv = (obj.get("talker") or obj.get("conversation_name") or obj.get("chat_name")
            or obj.get("room_name") or obj.get("session") or "未知会话")
    sender = (obj.get("sender") or obj.get("sender_name") or obj.get("from")
              or obj.get("sender_wxid") or "unknown")
    raw_time = (obj.get("time") or obj.get("create_time") or obj.get("msg_time")
                or obj.get("sent_at") or obj.get("time_str"))
    raw_id = (obj.get("id") or obj.get("local_id") or obj.get("msg_id")
              or obj.get("server_id") or obj.get("source_message_id"))
    m = {
        "source_platform": obj.get("source_platform") or platform,
        "conversation_id": str(obj.get("conversation_id") or conv),
        "conversation_name": str(conv),
        "sender_name": str(sender),
        "sent_at": _iso_time(raw_time),
        "text": text[:2000],
        "time_confidence": "exact" if raw_time else "capture_time",
    }
    m["source_message_id"] = str(raw_id) if raw_id else stable_id(m)
    return m


def post_batch(messages: list, url: str) -> dict:
    if not messages:
        return {"received": 0, "inserted": 0}
    data = json.dumps({"messages": messages}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def fresh_only(messages: list, st: dict) -> list:
    seen = set(st.get("seen", []))
    out, batch_seen = [], set()
    for m in messages:
        sid = m["source_message_id"]
        if sid in seen or sid in batch_seen:
            continue
        batch_seen.add(sid)
        out.append(m)
    return out


# ---------------- 上游：chatlog HTTP ----------------
def collect_http(args, st: dict) -> list:
    params = {"time": args.time or "today", "format": "json", "limit": args.limit}
    if args.talker:
        params["talker"] = args.talker
    url = args.chatlog_url + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
    except Exception as e:
        print(f"[http] 上游不可用：{type(e).__name__}: {e}", flush=True)
        return []
    try:
        data = json.loads(raw)
    except Exception:
        print("[http] 上游返回的不是 JSON", flush=True)
        return []
    if isinstance(data, dict):
        for k in ("data", "messages", "chatlog", "records", "items", "results"):
            if isinstance(data.get(k), list):
                data = data[k]
                break
        else:
            data = []
    if not isinstance(data, list):
        data = []
    return [normalize(o, args.platform) for o in data if normalize(o, args.platform)]


# ---------------- 上游：已解密 SQLite ----------------
def collect_sqlite(args, st: dict) -> list:
    if not args.db or not Path(args.db).exists():
        print(f"[sqlite] 数据库不存在：{args.db}", flush=True)
        return []
    try:
        conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
    except Exception as e:
        print(f"[sqlite] 打不开（可能仍加密）：{type(e).__name__}: {e}", flush=True)
        return []
    out = []
    try:
        for t in tables:
            cols = {r[1].lower() for r in conn.execute(f'PRAGMA table_info("{t}")')}
            time_col = next((c for c in ("create_time", "msg_time", "time", "timestamp",
                                         "msgtime", "send_time") if c in cols), None)
            text_col = next((c for c in ("content", "message", "text", "str_content",
                                         "msg_content") if c in cols), None)
            if not time_col or not text_col:
                continue
            q = (f'SELECT * FROM "{t}" WHERE "{time_col}" > ? '
                 f'ORDER BY "{time_col}" ASC LIMIT ?')
            for row in conn.execute(q, (st.get("last_db_time") or "", args.limit)):
                obj = dict(row)
                obj["time"] = obj.get(time_col)
                obj["content"] = obj.get(text_col)
                m = normalize(obj, args.platform)
                if m:
                    out.append(m)
    except Exception as e:
        print(f"[sqlite] 查询失败：{type(e).__name__}: {e}", flush=True)
    finally:
        conn.close()
    return out


# ---------------- 上游：JSONL 导出目录 ----------------
def collect_file(args, st: dict) -> list:
    folder = Path(args.dir or ROOT / "imports")
    offsets = st.setdefault("offsets", {})
    partial = st.setdefault("partial", {})
    out = []
    for p in sorted(glob.glob(str(folder / "**" / "*.jsonl"), recursive=True)):
        key = str(p)
        size = os.path.getsize(p)
        offset = offsets.get(key, 0)
        if size < offset:
            offset = 0
            partial.pop(key, None)
        if size == offset:
            continue
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            chunk = f.read()
            offsets[key] = f.tell()
        lines = (partial.pop(key, "") + chunk).split("\n")
        partial[key] = lines.pop()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            m = normalize(obj, args.platform)
            if m:
                out.append(m)
    if args.max_lines and len(out) > args.max_lines:
        out = out[-args.max_lines:]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="本地聊天数据 → MessageHub 桥接")
    ap.add_argument("--mode", choices=["http", "sqlite", "file"], default="http")
    ap.add_argument("--once", action="store_true", help="只跑一轮（默认行为，显式写法）")
    ap.add_argument("--loop", action="store_true", help="持续运行")
    ap.add_argument("--interval", type=float, default=10.0)
    ap.add_argument("--platform", default="wechat", choices=["wechat", "qq", "other"])
    ap.add_argument("--ingest-url", default=DEFAULT_INGEST)
    ap.add_argument("--chatlog-url", default=DEFAULT_CHATLOG)
    ap.add_argument("--time", default="today", help="chatlog 时间范围，如 today / 2026-11-15~2026-11-16")
    ap.add_argument("--talker", default=None)
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--db", default=None, help="已解密 SQLite 路径")
    ap.add_argument("--dir", default=None, help="JSONL 导出目录")
    ap.add_argument("--max-lines", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    collectors = {"http": collect_http, "sqlite": collect_sqlite, "file": collect_file}
    st = load_state()

    def once() -> dict:
        raw = collectors[args.mode](args, st)
        fresh = fresh_only(raw, st)
        if args.dry_run:
            print(json.dumps({"mode": args.mode, "fetched": len(raw),
                              "fresh": len(fresh), "sample": fresh[:1]},
                             ensure_ascii=False, indent=2))
            return {"inserted": 0}
        result = post_batch(fresh, args.ingest_url)
        if result.get("error"):
            print(f"[bridge] 推送失败：{result['error']}", flush=True)
            return result
        st["seen"].extend(m["source_message_id"] for m in fresh)
        if args.mode == "sqlite" and fresh:
            st["last_db_time"] = max(m["sent_at"] for m in fresh)
        save_state(st)
        print(json.dumps({"mode": args.mode, "fetched": len(raw),
                          "fresh": len(fresh), "result": result},
                         ensure_ascii=False), flush=True)
        return result

    once()
    if not args.loop:
        return 0
    while True:
        time.sleep(max(1.0, args.interval))
        try:
            once()
        except Exception as e:
            print(f"[bridge] 本轮异常：{type(e).__name__}: {e}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
