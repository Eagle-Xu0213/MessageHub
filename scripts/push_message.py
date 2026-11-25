"""向 MessageHub 标准接入点推送消息的命令行示例（仅用标准库）。

用法：
  # 单条
  python scripts/push_message.py --group "项目群" --sender "张三" --text "下午三点评审"
  # 指定精确时间（北京时间）
  python scripts/push_message.py --group "项目群" --sender "张三" --text "消息" \
      --sent-at "2026-11-15T11:30:00+08:00"
  # 批量：每行一个 JSON 对象的 JSONL 文件
  python scripts/push_message.py --file messages.jsonl
  # 批量：含 messages 数组的 JSON 文件
  python scripts/push_message.py --file messages.json --wrapper messages

任何能拿到微信 新消息的工具，都可以照这个格式调用即可实时入库。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_URL = "http://127.0.0.1:8000/api/live/ingest"
CN_TZ = timezone(timedelta(hours=8))


def post(payload, url: str) -> dict:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def load_file(path: Path) -> list:
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("["):
        return json.loads(stripped)
    if stripped.startswith("{"):
        return [json.loads(stripped)]
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="推送消息到 MessageHub 实时接入点")
    ap.add_argument("--url", default=DEFAULT_URL, help="接入点地址")
    ap.add_argument("--group", default="实时流入", help="会话名")
    ap.add_argument("--chat-id", default=None, help="会话 ID（稳定去重建议提供）")
    ap.add_argument("--sender", default="unknown", help="发送者")
    ap.add_argument("--text", default=None, help="消息正文")
    ap.add_argument("--platform", default="wechat", choices=["wechat", "other"])
    ap.add_argument("--sent-at", default=None, help="消息时间 ISO8601；不传则用采集时刻")
    ap.add_argument("--msg-id", default=None, help="平台消息 ID（有则按 ID 精确去重）")
    ap.add_argument("--file", default=None, help="批量文件（.jsonl 或 .json）")
    ap.add_argument("--wrapper", default="messages", help="批量提交时的包装字段名")
    args = ap.parse_args()

    if args.file:
        path = Path(args.file)
        if not path.exists():
            print(json.dumps({"error": f"文件不存在：{path}"}, ensure_ascii=False))
            return 1
        try:
            objs = load_file(path)
        except Exception as e:
            print(json.dumps({"error": f"文件解析失败：{type(e).__name__}: {e}"},
                             ensure_ascii=False))
            return 1
        payload = {args.wrapper: objs}
    else:
        if not args.text:
            print(json.dumps({"error": "请提供 --text 或 --file"}, ensure_ascii=False))
            return 1
        msg = {
            "source_platform": args.platform,
            "conversation_id": args.chat_id or args.group,
            "conversation_name": args.group,
            "sender_name": args.sender,
            "sent_at": args.sent_at or datetime.now(CN_TZ).isoformat(timespec="seconds"),
            "text": args.text,
            "time_confidence": "exact" if args.sent_at else "capture_time",
        }
        if args.msg_id:
            msg["source_message_id"] = args.msg_id
        payload = msg

    result = post(payload, args.url)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
