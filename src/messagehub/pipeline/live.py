"""实时消息接入通道（第一步）。

目标：让「新消息」持续、增量、可追溯地进入 MessageHub 的统一管道，
规则/分类留到下一阶段设计（本模块不做任何整理判断）。

支持三类数据源，可同时开启：
1. tail    —— 尾随 imports/live/*.jsonl 的新增行（不侵入客户端）。
2. uia     —— 微信 客户端 UIA 只读抓取。需窗口可见 + 已授权采集范围；
               allowed_chats 含 "*" 表示用户授权所有群；为空时只探测、不采集。
               实测微信 4.2 消息区为自绘层，UIA 取不到正文（见 uia_source 文档）。
3. ingest  —— 标准接入点 POST /api/live/ingest：外部工具推送后当轮入库，
               不依赖客户端控件结构，是当前唯一稳定可用的实时通道。

所有来源统一：normalize → fingerprint 去重 → SQLite，并写入采集状态与事件流。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from messagehub.pipeline.importer import insert_messages, make_fingerprint
from messagehub.adapters.uia_source import UIASource

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DB = Path(os.environ.get("MESSAGEHUB_DB_PATH") or (PROJECT_ROOT / "runtime" / "messagehub.db"))
DEFAULT_LIVE_DIR = PROJECT_ROOT / "imports" / "live"
CONFIG_FILE = PROJECT_ROOT / "runtime" / "live_config.json"
CN_TZ = timezone(timedelta(hours=8))
TAIL_EXTS = (".jsonl", ".ndjson")


def _now() -> str:
    return datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")


def _load_config() -> Dict[str, Any]:
    cfg = {"interval": 3.0, "sources": ["tail", "uia"], "allowed_chats": [],
           "max_messages_per_poll": 40}
    try:
        if CONFIG_FILE.exists():
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    env_chats = os.getenv("LIVE_ALLOWED_CHATS", "").strip()
    if env_chats:
        cfg["allowed_chats"] = [c.strip() for c in env_chats.split(",") if c.strip()]
    return cfg


def _save_config(cfg: Dict[str, Any]):
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_line(obj: dict) -> Optional[dict]:
    """把一行 JSON 规范化成统一消息格式；复用 registry 的解析器。"""
    from messagehub.adapters.registry import (
        parse_standard, parse_moect, parse_moect_formal,
    )
    if not isinstance(obj, dict):
        return None
    for fn in (parse_standard, parse_moect_formal, parse_moect):
        try:
            m = fn(obj)
        except Exception:
            m = None
        if m and m.get("text"):
            m["source_method"] = "live_tail"
            # 统一平台默认值：否则「同一条消息是否显式带 source_platform」会产生
            # 不同的 id:: 指纹，导致跨通道重复入库（实测 2026-11-15）。
            if not m.get("source_platform") or m.get("source_platform") == "unknown":
                m["source_platform"] = "wechat"
            if not m.get("sent_at"):
                m["sent_at"] = datetime.now(CN_TZ).isoformat(timespec="seconds")
                m["time_confidence"] = "capture_time"
            # 上游若明确标注了时间置信度（例如接入点用的是采集时刻），不要覆盖成 exact
            if obj.get("time_confidence"):
                m["time_confidence"] = str(obj["time_confidence"])
            return m
    # 兜底：极简字段映射（第三方工具只给最少字段时）
    text = obj.get("text") or obj.get("content") or obj.get("msg") or ""
    if not str(text).strip():
        return None
    return {
        "source_platform": obj.get("platform") or obj.get("source_platform") or "wechat",
        "source_method": "live_tail",
        "conversation_id": str(obj.get("conversation_id") or obj.get("chat") or "live_stream"),
        "conversation_name": str(obj.get("conversation_name") or obj.get("chat_name") or "实时流入"),
        "sender_name": str(obj.get("sender_name") or obj.get("sender") or "unknown"),
        "sent_at": str(obj.get("sent_at") or obj.get("time") or
                       datetime.now(CN_TZ).isoformat(timespec="seconds")),
        "text": str(text),
        "message_type": "text",
        "source_message_id": obj.get("source_message_id") or obj.get("id"),
        "time_confidence": "exact" if (obj.get("sent_at") or obj.get("time")) else "capture_time",
        "capture_status": "full",
    }


class LiveCollector:
    """后台实时采集器（单例使用）。线程安全，启动/停止不阻塞 Web 请求。"""

    def __init__(self, db_path: str = None, live_dir: str = None):
        self.db_path = str(db_path or DEFAULT_DB)
        self.live_dir = Path(live_dir or DEFAULT_LIVE_DIR)
        self.live_dir.mkdir(parents=True, exist_ok=True)
        self.cfg = _load_config()
        self._offsets: Dict[str, int] = {}
        self._partial: Dict[str, str] = {}
        self._events: Deque[Dict[str, Any]] = deque(maxlen=300)
        self._event_seq = 0
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.state: Dict[str, Any] = {
            "running": False,
            "started_at": None,
            "last_poll": None,
            "cycles": 0,
            "total_new": 0,
            "last_error": None,
            "sources": {
                "tail": {"enabled": "tail" in self.cfg["sources"], "status": "idle",
                         "files_tracked": 0, "new": 0, "last_error": None},
                "uia": {"enabled": "uia" in self.cfg["sources"], "status": "idle",
                        "new": 0, "last_error": None, "reason": None},
                "ingest": {"enabled": True, "status": "idle", "new": 0,
                           "received": 0, "last_error": None, "last_at": None},
            },
        }

    # ---------------- 配置 ----------------
    def configure(self, interval: float = None, sources: List[str] = None,
                  allowed_chats: List[str] = None) -> Dict[str, Any]:
        with self._lock:
            if interval is not None:
                self.cfg["interval"] = max(0.5, min(float(interval), 60.0))
            if sources is not None:
                self.cfg["sources"] = [s for s in sources if s in ("tail", "uia")]
            if allowed_chats is not None:
                self.cfg["allowed_chats"] = [c.strip() for c in allowed_chats if c.strip()]
            _save_config(self.cfg)
            self.state["sources"]["tail"]["enabled"] = "tail" in self.cfg["sources"]
            self.state["sources"]["uia"]["enabled"] = "uia" in self.cfg["sources"]
            return dict(self.cfg)

    # ---------------- 生命周期 ----------------
    def start(self) -> Dict[str, Any]:
        if self._thread and self._thread.is_alive():
            return {"started": False, "reason": "already running"}
        self._stop_event.clear()
        self.state["running"] = True
        self.state["started_at"] = _now()
        self._thread = threading.Thread(target=self._loop, name="messagehub-live", daemon=True)
        self._thread.start()
        return {"started": True, "config": dict(self.cfg)}

    def stop(self) -> Dict[str, Any]:
        self._stop_event.set()
        self.state["running"] = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        return {"stopped": True}

    def _loop(self):
        while not self._stop_event.is_set():
            try:
                self.poll_once()
            except Exception as e:
                self.state["last_error"] = f"{type(e).__name__}: {e}"
            self._stop_event.wait(max(0.5, float(self.cfg.get("interval", 3.0))))

    # ---------------- 采集一轮 ----------------
    def poll_once(self) -> Dict[str, Any]:
        new_total = 0
        if "tail" in self.cfg["sources"]:
            new_total += self._poll_tail()
        if "uia" in self.cfg["sources"]:
            new_total += self._poll_uia()
        with self._lock:
            self.state["cycles"] += 1
            self.state["total_new"] += new_total
            self.state["last_poll"] = _now()
        return {"new": new_total, "cycles": self.state["cycles"]}

    def _new_only(self, candidates: List[dict]) -> List[dict]:
        """过滤掉指纹已存在的消息，保证事件流与入库数量一致。"""
        if not candidates:
            return []
        conn = sqlite3.connect(self.db_path)
        try:
            fresh = []
            seen = set()
            for m in candidates:
                fp = make_fingerprint(m)
                if fp in seen:          # 同一批次内的重复也只保留一条
                    continue
                seen.add(fp)
                if conn.execute("SELECT 1 FROM messages WHERE fingerprint=?", (fp,)).fetchone():
                    continue
                fresh.append(m)
            return fresh
        finally:
            conn.close()

    def _commit(self, fresh: List[dict], source: str) -> int:
        if not fresh:
            return 0
        conn = sqlite3.connect(self.db_path)
        try:
            stats = insert_messages(conn, fresh)
        finally:
            conn.close()
        inserted = stats["inserted"]
        with self._lock:
            for m in fresh[:inserted]:
                self._event_seq += 1
                self._events.append({
                    "seq": self._event_seq,
                    "ts": _now(),
                    "source": source,
                    "platform": m.get("source_platform"),
                    "conversation": m.get("conversation_name"),
                    "sender": m.get("sender_name"),
                    "text": str(m.get("text") or "")[:200],
                    "sent_at": m.get("sent_at"),
                    "time_confidence": m.get("time_confidence"),
                })
            self.state["sources"][source]["new"] += inserted
        return inserted

    def ingest(self, objects: Any, archive: bool = True) -> Dict[str, Any]:
        """标准接入点：外部工具推送一条/一批消息，立即规范化 → 去重 → 入库 → 事件流。

        与 tail 的区别：不依赖文件轮询，POST 返回时消息已入库并进入 SSE 流。
        归档写入 live_dir/archive/ 子目录（tail 只扫顶层文件，不会重复处理）。
        """
        if isinstance(objects, dict):
            objects = [objects]
        if not isinstance(objects, list):
            return {"received": 0, "parsed": 0, "inserted": 0, "duplicates": 0,
                    "error": "请求体必须是 JSON 对象或对象数组"}
        objects = [o for o in objects if isinstance(o, dict)][:1000]
        batch: List[dict] = []
        for obj in objects:
            try:
                m = _normalize_line(obj)
            except Exception:
                m = None
            if m:
                m["source_method"] = "live_ingest"
                batch.append(m)
        fresh = self._new_only(batch)
        n = self._commit(fresh, "ingest")
        with self._lock:
            self.state["total_new"] += n
            st = self.state["sources"]["ingest"]
            st["status"] = "ok_new" if n else "ok_no_new"
            st["received"] = int(st.get("received") or 0) + len(objects)
            st["last_at"] = _now()
            st["last_error"] = None
            seq = self._event_seq
        if archive and fresh:
            try:
                day = datetime.now(CN_TZ).strftime("%Y%m%d")
                folder = self.live_dir / "archive"
                folder.mkdir(parents=True, exist_ok=True)
                with (folder / f"ingest_{day}.jsonl").open("a", encoding="utf-8") as f:
                    for m in fresh:
                        f.write(json.dumps(m, ensure_ascii=False) + "\n")
            except Exception as e:
                with self._lock:
                    self.state["sources"]["ingest"]["last_error"] = (
                        f"归档失败：{type(e).__name__}: {e}")
        return {"received": len(objects), "parsed": len(batch), "inserted": n,
                "duplicates": len(batch) - n, "last_seq": seq}

    def _poll_tail(self) -> int:
        st = self.state["sources"]["tail"]
        try:
            files = sorted(p for p in self.live_dir.glob("*") if p.suffix.lower() in TAIL_EXTS)
            batch: List[dict] = []
            for path in files:
                key = str(path)
                size = path.stat().st_size
                offset = self._offsets.get(key, 0)
                if size < offset:            # 文件被截断/重写 → 从头读
                    offset = 0
                    self._partial.pop(key, None)
                if size == offset:
                    continue
                with path.open("r", encoding="utf-8", errors="replace") as f:
                    f.seek(offset)
                    chunk = f.read()
                    new_offset = f.tell()
                leftover = self._partial.pop(key, "")
                data = leftover + chunk
                lines = data.split("\n")
                self._partial[key] = lines.pop()      # 最后一行可能未写完
                self._offsets[key] = new_offset
                for line in lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    m = _normalize_line(obj)
                    if m:
                        batch.append(m)
            st["files_tracked"] = len(files)
            fresh = self._new_only(batch)
            n = self._commit(fresh, "tail")
            st["status"] = "ok_new" if n else "ok_no_new"
            st["last_error"] = None
            return n
        except Exception as e:
            st["status"] = "error"
            st["last_error"] = f"{type(e).__name__}: {e}"
            return 0

    def _poll_uia(self) -> int:
        st = self.state["sources"]["uia"]
        try:
            reader = UIASource(
                allowed_chats=self.cfg.get("allowed_chats") or [],
                max_messages=int(self.cfg.get("max_messages_per_poll", 40)),
            )
            result = reader.read()
            st["reason"] = result.get("reason")
            st["probe"] = result.get("probe", {})
            st["details"] = result.get("details", [])
            st["structure"] = result.get("structure", [])
            status = result.get("status", "error")
            if status == "ok":
                fresh = self._new_only(result["messages"])
                n = self._commit(fresh, "uia")
                st["status"] = "ok_new" if n else "ok_no_new"
            else:
                st["status"] = status
                n = 0
            st["last_error"] = None
            return n
        except Exception as e:
            st["status"] = "error"
            st["last_error"] = f"{type(e).__name__}: {e}"
            return 0

    # ---------------- 查询 ----------------
    def status(self) -> Dict[str, Any]:
        with self._lock:
            s = dict(self.state)
            s["sources"] = {k: dict(v) for k, v in self.state["sources"].items()}
            s["last_seq"] = self._event_seq
        s["config"] = dict(self.cfg)
        s["live_dir"] = str(self.live_dir)
        s["live_dir_files"] = [p.name for p in self.live_dir.glob("*")
                               if p.suffix.lower() in TAIL_EXTS]
        s["server_time"] = _now()
        return s

    def events_since(self, seq: int, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            return [e for e in self._events if e["seq"] > seq][:limit]

    def recent(self, limit: int = 30) -> List[Dict[str, Any]]:
        """服务重启后内存事件为空，从数据库回填最近的实时消息，避免页面显示空白。"""
        with self._lock:
            items = list(self._events)[-limit:][::-1]
        if items:
            return items
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT id, source_platform, conversation_name, sender_name, sent_at, "
                "text, time_confidence FROM messages "
                "WHERE source_method IN ('live_tail','uia_readonly','live_ingest') "
                # 按消息实际时间排序：id 只反映入库顺序，历史验证数据重新入库时
                # id 更大却更旧，会把重启后的实时流顶部占满。
                "ORDER BY sent_at DESC, id DESC LIMIT ?", (limit,),
            ).fetchall()
            conn.close()
            return [{
                "seq": 0,
                "ts": (r["sent_at"] or "")[:19].replace("T", " ") or "时间未知",
                "source": "history",
                "platform": r["source_platform"],
                "conversation": r["conversation_name"],
                "sender": r["sender_name"],
                "text": str(r["text"] or "")[:200],
                "sent_at": r["sent_at"],
                "time_confidence": r["time_confidence"],
            } for r in rows]
        except Exception:
            return []


_collector: Optional[LiveCollector] = None


def get_collector() -> LiveCollector:
    global _collector
    if _collector is None:
        _collector = LiveCollector()
    return _collector
