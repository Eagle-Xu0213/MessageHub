"""Local receive-only OneBot connection."""
from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import sqlite3
from pathlib import Path
from contextlib import closing
from datetime import datetime, timedelta

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from messagehub import personal
from messagehub.config import BASE_DIR, CN_TZ
from messagehub.database import get_db
from messagehub.pipeline.importer import insert_messages
from messagehub.task_pipeline import _time

router = APIRouter()
CONFIG_PATH = BASE_DIR / "runtime" / "qq_onebot.json"
SOURCE = "qq_onebot"
STATE_PATH = BASE_DIR / "runtime" / "qq_receiver.json"
SUPERVISOR_PATH = BASE_DIR / "runtime" / "qq_supervisor.json"
IDLE_TIMEOUT = 100
catalog: dict[str, dict] = {}
_connection: WebSocket | None = None
_connected = False
_last_message_at: str | None = None
_disconnected_at: str | None = None
_disconnect_reason: str | None = None
_last_activity = 0.0


def _read_state(path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _record(**changes):
    state = {**_read_state(STATE_PATH), **changes}
    try:
        temporary = STATE_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(state), encoding="utf-8")
        temporary.replace(STATE_PATH)
    except OSError:
        # Diagnostics must never interrupt capture.
        pass


def _stamp():
    return datetime.now(CN_TZ).isoformat()


def _config() -> dict:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if data.get("enabled") is True and isinstance(data.get("token"), str) and data["token"] and _id(data.get("account")):
            return data
    except (OSError, ValueError, AttributeError):
        pass
    return {}


def _id(value) -> str:
    if type(value) not in (int, str):
        return ""
    value = str(value)
    return value if 0 < len(value) <= 20 and value.isascii() and value.isdecimal() and 0 < int(value) else ""


def conversation_id(account: str, kind: str, peer: str) -> str:
    return f"qq:{kind}:" + json.dumps([account, peer], separators=(",", ":"))


def local_group_activity() -> dict[str, int]:
    """Read SnowLuma's observed group timestamps without querying message content."""
    config = _config()
    root = config.get("snowluma_data_dir")
    account = _id(config.get("account"))
    if not isinstance(root, str) or not root or not account:
        return {}
    path = Path(root).resolve() / account / "messages.db"
    if not path.is_file():
        return {}
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=0.25)) as conn:
            rows = conn.execute("""SELECT session_id, MAX(timestamp) FROM messages
                WHERE is_group=1 AND event_name='group_message' AND timestamp>0 GROUP BY session_id""")
            return {conversation_id(account, "group", peer): timestamp
                    for session, timestamp in rows if (peer := _id(session))}
    except (OSError, sqlite3.Error):
        return {}


@router.get("/api/qq/status")
async def qq_status():
    receiver = _read_state(STATE_PATH)
    supervisor = _read_state(SUPERVISOR_PATH)
    updated = _time(supervisor.get("updated_at"))
    fresh = updated and datetime.now(CN_TZ) - updated < timedelta(seconds=20)
    phase = supervisor.get("phase")
    config = _config()
    engine = "snowluma" if config.get("snowluma_data_dir") else "onebot"
    state = "disabled"
    if config:
        if _connected:
            state = "connected"
        elif _connection is not None or (fresh and phase == "waiting_handshake"):
            state = "waiting_handshake"
        elif fresh and phase in ("waiting_login", "restarting"):
            state = phase
        elif _disconnected_at or receiver.get("disconnected_at"):
            state = "disconnected"
        else:
            state = "stopped"
    return {"state": state, "engine": engine, "last_message_at": _last_message_at or receiver.get("last_message_at"),
            "disconnected_at": _disconnected_at or receiver.get("disconnected_at"),
            "disconnect_reason": _disconnect_reason or receiver.get("disconnect_reason"),
            "last_heartbeat_at": receiver.get("last_heartbeat_at"),
            "handshake_failure": receiver.get("handshake_failure"),
            "process_exit_code": supervisor.get("exit_code"),
            "process_exited_at": supervisor.get("exited_at"),
            "process_issue": supervisor.get("issue") if fresh else None}


async def _query(socket: WebSocket, action: str):
    await socket.send_json({"action": action, "params": {}, "echo": action})
    async with asyncio.timeout(8):
        while True:
            event = await socket.receive_json()
            if isinstance(event, dict) and event.get("echo") == action:
                if event.get("status") != "ok" or event.get("retcode") != 0:
                    raise ValueError("OneBot query failed")
                return event.get("data")


def _directory(account: str, groups, friends) -> dict[str, dict]:
    entries = {}
    for kind, rows, id_key, name_key in (
        ("group", groups, "group_id", "group_name"),
        ("contact", friends, "user_id", "nickname"),
    ):
        if not isinstance(rows, list):
            raise ValueError("Invalid OneBot directory")
        for row in rows:
            if not isinstance(row, dict) or not (peer := _id(row.get(id_key))):
                continue
            identity = conversation_id(account, kind, peer)
            name = (row.get("remark") if kind == "contact" else None) or row.get(name_key)
            entries[identity] = {"id": identity, "name": name if isinstance(name, str) and name else peer,
                                 "platform": "qq", "kind": kind, "identity_hint": f"{account[-4:]} / {peer[-4:]}"}
    return entries


def _event_activity(event, account: str, directory: dict) -> tuple[str, str, datetime] | None:
    if not isinstance(event, dict) or event.get("post_type") != "message" or _id(event.get("self_id")) != account:
        return None
    source = event.get("message_type")
    sender = _id(event.get("user_id"))
    if not sender or sender == account:
        return None
    if source == "group":
        kind, peer = "group", _id(event.get("group_id"))
    elif source == "private" and event.get("sub_type") == "friend":
        kind, peer = "contact", sender
    else:
        return None
    identity = conversation_id(account, kind, peer)
    timestamp = event.get("time")
    if identity not in directory or type(timestamp) is not int:
        return None
    try:
        sent = datetime.fromtimestamp(timestamp, tz=CN_TZ)
    except (OSError, ValueError, OverflowError):
        return None
    return identity, sender, sent


def _message(event, account: str, directory: dict) -> dict | None:
    activity = _event_activity(event, account, directory)
    if activity is None:
        return None
    identity, sender, sent = activity
    row = directory[identity]
    row["last_received"] = max(row.get("last_received", 0), sent.timestamp())
    selection = (personal._CONFIG.get("qq_scope") or {}).get(identity) or {}
    since = _time(selection.get("since"))
    sid = event.get("message_id")
    if (selection.get("level") not in ("work", "watch") or not since
            or sent < since or type(sid) is not int):
        return None
    segments = event.get("message")
    if not isinstance(segments, list):
        return None
    parts = []
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get("data"), dict):
            continue
        data = segment["data"]
        if segment.get("type") == "text" and isinstance(data.get("text"), str):
            parts.append(data["text"])
        elif segment.get("type") == "at":
            target = _id(data.get("qq"))
            if target:
                parts.append(f" @{target} ")
    body = "".join(parts).strip()
    if not body:
        return None
    details = event.get("sender")
    name = (details.get("card") or details.get("nickname")) if isinstance(details, dict) else None
    return {"source_platform": "qq", "source_method": SOURCE, "account_ref": account,
            "conversation_id": identity, "conversation_name": directory[identity]["name"],
            "sender_ref": sender, "sender_name": name if isinstance(name, str) and name else sender,
            "sent_at": sent.isoformat(), "time_confidence": "exact", "text": body, "source_message_id": str(sid)}


@router.websocket("/api/qq/onebot/ws")
async def qq_websocket(socket: WebSocket):
    global _connection, _connected, _last_message_at, _disconnected_at, _disconnect_reason, _last_activity
    config = _config()
    try:
        local = bool(socket.client and ipaddress.ip_address(socket.client.host).is_loopback)
    except ValueError:
        local = False
    authorization = socket.headers.get("authorization", "")
    if not local or not config or not hmac.compare_digest(
        authorization.encode(), ("Bearer " + config["token"]).encode()
    ):
        await socket.close(code=1008)
        return
    loop = asyncio.get_running_loop()
    previous = _connection
    if previous is not None and loop.time() - _last_activity < IDLE_TIMEOUT:
        await socket.close(code=1008)
        return
    _connection = socket
    _connected = False
    _last_activity = loop.time()
    if previous is not None:
        try:
            await previous.close(code=1013)
        except (RuntimeError, WebSocketDisconnect):
            pass
    account = str(config["account"])
    reason = "receiver_error"
    phase = "login_info"
    try:
        await socket.accept()
        login = await _query(socket, "get_login_info")
        if not isinstance(login, dict) or _id(login.get("user_id")) != account:
            reason = "account_mismatch"
            await socket.close(code=1008)
            return
        phase = "group_list"
        groups = await _query(socket, "get_group_list")
        phase = "friend_list"
        friends = await _query(socket, "get_friend_list")
        directory = _directory(account, groups, friends)
        for key, row in directory.items():
            if key in catalog:
                row["last_received"] = catalog[key].get("last_received", 0)
        catalog.clear()
        catalog.update(directory)
        phase = "receiving"
        _connected = True
        _record(handshake_failure=None)
        _last_activity = loop.time()
        while _connection is socket:
            try:
                event = await asyncio.wait_for(socket.receive_json(), timeout=IDLE_TIMEOUT)
            except TimeoutError:
                reason = "heartbeat_timeout"
                await socket.close(code=1013)
                return
            if _connection is not socket:
                return
            _last_activity = loop.time()
            if (isinstance(event, dict) and event.get("post_type") == "meta_event"
                    and event.get("meta_event_type") == "heartbeat" and _id(event.get("self_id")) == account):
                _record(last_heartbeat_at=_stamp())
            if _config() != config:
                reason = "config_changed"
                await socket.close(code=1008)
                return
            message = _message(event, account, directory)
            if message:
                with closing(get_db()) as conn:
                    if insert_messages(conn, [message])["inserted"]:
                        _last_message_at = _stamp()
                        _record(last_message_at=_last_message_at)
    except WebSocketDisconnect as exc:
        reason = "transport_lost" if exc.code == 1006 else "peer_closed"
    except (TimeoutError, ValueError):
        reason = "protocol_error"
        await socket.close(code=1008)
    except asyncio.CancelledError:
        reason = "receiver_stopped"
        raise
    finally:
        if _connection is socket:
            if _connected:
                _disconnected_at = _stamp()
                _disconnect_reason = reason
                _record(disconnected_at=_disconnected_at, disconnect_reason=reason)
            else:
                _record(handshake_failure={"stage": phase, "reason": reason, "at": _stamp()})
            _connected = False
            _connection = None
