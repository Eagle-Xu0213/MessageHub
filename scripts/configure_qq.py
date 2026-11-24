"""Configure MessageHub's loopback receiver for an existing OneBot engine."""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "ws://127.0.0.1:8000/api/qq/onebot/ws"


def read_config(path: Path) -> dict:
    if not path.exists():
        return {}
    result = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(result, dict):
        raise ValueError("Configuration must be a JSON object")
    return result


def write_config(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False, encoding="utf-8") as temp:
        temporary = Path(temp.name)
        try:
            json.dump(data, temp, ensure_ascii=False, indent=2)
            temp.write("\n")
            temp.close()
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def configure(receiver: Path, account: str, snowluma_root: Path | None = None) -> None:
    if not re.fullmatch(r"[1-9][0-9]{4,19}", account):
        raise ValueError("Please enter the QQ account number")
    current = read_config(receiver)
    if current.get("account") and str(current["account"]) != account:
        raise ValueError("A different QQ account is already configured")
    target = dict(current)
    token = current.get("token") or secrets.token_urlsafe(32)
    if not isinstance(token, str):
        raise ValueError("The existing receiver token is invalid")
    target.update(enabled=True, account=account, token=token)
    if snowluma_root is None:
        write_config(receiver, target)
        return

    root = snowluma_root.resolve(strict=True)
    if not (root / "index.mjs").is_file() or not (root / "launcher.bat").is_file():
        raise ValueError("Choose the folder containing the extracted SnowLuma runtime")
    engine_path = root / "config" / f"onebot_{account}.json"
    engine = read_config(engine_path)
    networks = engine.setdefault("networks", {})
    for key in ("httpServers", "httpClients", "wsServers", "wsClients"):
        if not isinstance(networks.setdefault(key, []), list):
            raise ValueError("Unexpected OneBot network configuration")
    clients = networks["wsClients"]
    matches = [client for client in clients if client.get("url") == ENDPOINT]
    if len(matches) > 1:
        raise ValueError("Duplicate MessageHub connections already exist")
    if matches:
        connection = matches[0]
    else:
        names = {client.get("name") for client in clients}
        name = "messagehub"
        while name in names:
            name += "-local"
        connection = {"name": name}
        clients.append(connection)
    connection.update(enabled=True, url=ENDPOINT, role="Universal",
                      accessToken=token, messageFormat="array", reportSelfMessage=False,
                      reconnectIntervalMs=5000)
    engine.setdefault("historySync", {"enabled": False})
    engine.setdefault("statusCommand", {"enabled": False})
    target["snowluma_data_dir"] = str(root / "data")
    write_config(engine_path, engine)
    write_config(receiver, target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", help="QQ account, defaults to the previously configured account")
    parser.add_argument("--snowluma-root", type=Path, help="An existing SnowLuma installation folder")
    args = parser.parse_args()
    receiver = ROOT / "runtime" / "qq_onebot.json"
    try:
        account = args.account or str(read_config(receiver).get("account") or "")
        configure(receiver, account, args.snowluma_root)
    except (OSError, TypeError, AttributeError, ValueError) as exc:
        if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError):
            parser.exit(1, str(exc) + "\n")
        parser.exit(1, "Could not read or update the local QQ configuration.\n")
    if args.snowluma_root:
        print("Local QQ connection configured. Open SnowLuma and connect your QQ client.")
    else:
        print("Receiver configured. Use its local configuration token in your engine's reverse WebSocket client.")


if __name__ == "__main__":
    main()
