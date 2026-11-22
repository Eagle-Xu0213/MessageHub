"""简报邮件桥：把每日简报以 HTML 邮件发送到指定收件箱。

为什么用邮件：没有具体时间的事件不适合塞进日历，而邮件能被各种
AI 助手读取并统一规划日程——简报发到邮箱，后续处理交给读邮件的工具。

配置（.env，全部可选；不配则只在网页显示简报，不发信）：
  MESSAGEHUB_EMAIL_ENABLED=1
  SMTP_HOST=smtp.qq.com
  SMTP_PORT=465
  SMTP_USER=your-account@qq.com
  SMTP_PASS=<邮箱授权码，不是登录密码>
  BRIEF_TO=your-inbox@example.com

调用方：schedule.register_notify_hook(send_brief_email) —— 简报有实质
内容时自动发信；手动重发走 send_brief_email(brief, force=True)。
"""
from __future__ import annotations

import html
import json
import tempfile
import threading
import os
import smtplib
import ssl
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, Optional

# 显式加载项目 .env（工作目录无关）
try:
    from dotenv import load_dotenv
    _p = Path(__file__).resolve().parents[2] / ".env"
    if _p.exists() and os.environ.get('MESSAGEHUB_DISABLE_DOTENV') != '1':
        load_dotenv(str(_p), override=False)
except ImportError:
    pass


_settings_lock = threading.Lock()


def _settings_path() -> Path:
    from messagehub.config import DB_PATH
    return DB_PATH.parent / "email_settings.json"


def _settings() -> dict:
    try:
        return json.loads(_settings_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {
            "enabled": os.getenv("MESSAGEHUB_EMAIL_ENABLED") == "1",
            "host": os.getenv("SMTP_HOST", "smtp.qq.com"),
            "port": os.getenv("SMTP_PORT", "465"),
            "user": os.getenv("SMTP_USER", ""),
            "pass": os.getenv("SMTP_PASS", ""),
            "to": os.getenv("BRIEF_TO", ""),
        }
    except (OSError, ValueError):
        return {"enabled": False}


def _ready(cfg: dict) -> bool:
    try:
        return 1 <= int(cfg["port"]) <= 65535 and all(
            cfg[key].strip() for key in ("host", "user", "pass", "to")
        )
    except (KeyError, TypeError, ValueError):
        return False


def public_settings() -> dict:
    cfg = _settings()
    return {
        **{key: cfg.get(key, "") for key in ("host", "port", "user", "to")},
        "enabled": bool(cfg.get("enabled")),
        "has_password": bool(cfg.get("pass")),
    }


def save_settings(data: dict) -> None:
    if not isinstance(data, dict) or type(data.get("enabled")) is not bool:
        raise ValueError("请选择是否启用邮件通知。")
    cfg = {"enabled": data["enabled"]}
    for key in ("host", "port", "user", "pass", "to"):
        value = data.get(key, "")
        if not isinstance(value, str) or len(value) > 512 or any(c in value for c in "\r\n\x00"):
            raise ValueError("邮件配置格式无效。")
        cfg[key] = value.strip()
    if cfg["port"] and (not cfg["port"].isdigit() or not 1 <= int(cfg["port"]) <= 65535):
        raise ValueError("端口须为 1 至 65535。")
    with _settings_lock:
        if not cfg["pass"]:
            old = _settings()
            if cfg["host"] == old.get("host") and cfg["user"] == old.get("user"):
                cfg["pass"] = old.get("pass", "")
        if cfg["enabled"] and not _ready(cfg):
            raise ValueError("启用前请填写完整的 SMTP 设置与授权码。")
        path = _settings_path()
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=path.parent, delete=False) as handle:
            temp = Path(handle.name)
            try:
                json.dump(cfg, handle, ensure_ascii=False)
                handle.close()
                temp.replace(path)
            finally:
                temp.unlink(missing_ok=True)


def email_status() -> str:
    cfg = _settings()
    if not cfg.get("enabled"):
        return "disabled"
    return "ready" if _ready(cfg) else "incomplete"


def _smtp_config() -> Optional[Dict[str, str]]:
    cfg = _settings()
    return cfg if cfg.get("enabled") and _ready(cfg) else None


def render_brief_html(brief: Dict[str, Any]) -> str:
    """简报 → 邮件 HTML（分栏、字段可溯源，邮件客户端里也好读）。"""
    from html import escape as e

    def item(ev: Dict[str, Any]) -> str:
        ddl = f'<div style="color:#d9381e;font-size:12px;margin-top:4px;">⏰ {e(ev["deadline"])}</div>' if ev.get("deadline") else ""
        rep = f'<div style="color:#389e0d;font-size:12px;margin-top:4px;">↳ 用户已回复：{e(ev["reply_evidence"])}</div>' if ev.get("reply_evidence") else ""
        return (
            f'<div style="padding:8px 0;border-bottom:1px solid #eee;">'
            f'<div style="font-size:12px;color:#8a94a6;">'
            f'<b style="color:#4f46e5;">{e(ev["group"])}</b> · {e(ev["sender"])} · {e((ev["sent_at"] or "")[:16])}</div>'
            f'<div style="font-size:14px;color:#333;margin-top:2px;">{e(ev["text"])}</div>'
            f'{ddl}{rep}</div>'
        )

    s = brief.get("sections", {})
    st = brief.get("stats", {})
    todo_html = "".join(item(x) for x in s.get("todos", [])) or '<div style="color:#999;">今日无待办</div>'
    reply_html = "".join(item(x) for x in s.get("replies", [])) or '<div style="color:#999;">无待回复</div>'
    notice_html = "".join(item(x) for x in s.get("notices", [])) or '<div style="color:#999;">无通知</div>'
    resolved_html = "".join(item(x) for x in s.get("resolved", []))

    resolved_block = ""
    if s.get("resolved"):
        resolved_block = (
            '<h3 style="color:#389e0d;font-size:15px;margin:20px 0 8px;">✅ 已处理（闭环）</h3>'
            + resolved_html)

    return f"""<div style="font-family: -apple-system, 'Microsoft YaHei', sans-serif; max-width:640px; margin:0 auto; color:#2c3e50;">
  <h2 style="color:#1a73e8;">📋 MessageHub 每日简报 · {e(brief.get("date", ""))}</h2>
  <div style="background:#f0fdf4;border-left:4px solid #16a34a;padding:12px 14px;border-radius:8px;font-size:14px;line-height:1.7;margin:14px 0;">
    {e(brief.get("ai_summary", ""))}
  </div>
  <div style="font-size:12px;color:#8a94a6;margin-bottom:16px;">
    待办 {st.get("todos", 0)} · 需回复 {st.get("replies", 0)} · 通知 {st.get("notices", 0)} · 已闭环 {st.get("resolved", 0)}
  </div>
  <h3 style="color:#d9381e;font-size:15px;margin:16px 0 8px;">🔴 待办（需要你动手）</h3>
  {todo_html}
  <h3 style="color:#d48806;font-size:15px;margin:20px 0 8px;">🟡 需你回复</h3>
  {reply_html}
  <h3 style="color:#1668dc;font-size:15px;margin:20px 0 8px;">🔵 通知</h3>
  {notice_html}
  {resolved_block}
  <hr style="margin-top:24px;border:none;border-top:1px solid #eee;">
  <div style="font-size:11px;color:#aaa;">
    由 MessageHub 规则引擎自动生成（{e(brief.get("generated_at", ""))}）·
    事件来自真实群消息，可在 http://127.0.0.1:8000/brief 溯源 ·
    此邮件同时送达配置的收件箱，供后续日程规划使用
  </div>
</div>"""


def send_brief_email(brief: Dict[str, Any], force: bool = False) -> Dict[str, Any]:
    """发送简报邮件。返回 {sent, detail}。

    force=True 忽略"空简报不发"的保护（手动重发用）。
    """
    cfg = _smtp_config()
    if not cfg:
        return {"sent": False, "detail": "SMTP 未配置（.env 缺 SMTP_USER/PASS/BRIEF_TO）"}

    stats = brief.get("stats", {})
    if not force and not any(stats.get(k) for k in ("todos", "replies", "notices")):
        return {"sent": False, "detail": "空简报，按设计不发（无事静默）"}

    date = brief.get("date", "")
    n = stats.get("todos", 0) + stats.get("replies", 0) + stats.get("notices", 0)
    subject = f"[MessageHub] 每日简报 {date} · 待办{stats.get('todos', 0)}/通知{stats.get('notices', 0)}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = cfg["user"]
    msg["To"] = cfg["to"]
    msg.attach(MIMEText(render_brief_html(brief), "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL(cfg["host"], int(cfg["port"]), timeout=20, context=ssl.create_default_context()) as s:
            s.login(cfg["user"], cfg["pass"])
            s.sendmail(cfg["user"], [cfg["to"]], msg.as_string())
        print(f"[brief-mail] 已发送 {subject} → {cfg['to']}")
        return {"sent": True, "detail": f"{subject} → {cfg['to']}"}
    except Exception as e:
        return {"sent": False, "detail": type(e).__name__}


def _event_row(ev: Dict[str, Any], show_tag: bool = False) -> str:
    """整理/加急邮件里的一条事件行。"""
    from html import escape as e
    tag = ""
    if show_tag and ev.get("semantic_tag"):
        tag = (f'<span style="display:inline-block;background:#eef2ff;color:#4338ca;'
               f'font-size:11px;padding:1px 7px;border-radius:10px;margin-right:6px;">'
               f'{e(str(ev["semantic_tag"]))}</span>')
    ddl = ""
    if ev.get("deadline"):
        ddl = f'<div style="color:#d9381e;font-size:12px;margin-top:3px;">⏰ {e(ev["deadline"])}</div>'
    reason = ""
    if ev.get("urgent_reason"):
        reason = (f'<div style="color:#b45309;font-size:12px;margin-top:3px;">'
                  f'⚡ 加急判定：{e(ev["urgent_reason"])}</div>')
    return (
        f'<div style="padding:9px 0;border-bottom:1px solid #f0f0f0;">'
        f'<div style="font-size:12px;color:#8a94a6;">{tag}'
        f'<b style="color:#4f46e5;">{e(ev.get("conversation_name") or "")}</b> · '
        f'{e(ev.get("sender_name") or "")} · {e((ev.get("sent_at") or "")[:16])}</div>'
        f'<div style="font-size:14px;color:#333;margin-top:3px;">{e(ev.get("text") or "")}</div>'
        f'{ddl}{reason}</div>'
    )


def render_digest_html(payload: Dict[str, Any]) -> str:
    """唤醒补扫整理邮件：按语义标签分组，已闭环折叠在末尾。"""
    from html import escape as e
    groups = payload.get("groups", {})
    st = payload.get("stats", {})
    blocks = []
    for tag, events in groups.items():
        rows = "".join(_event_row(ev, show_tag=False) for ev in events)
        blocks.append(
            f'<div style="margin:16px 0;">'
            f'<h3 style="color:#1a73e8;font-size:14px;margin:0 0 6px;">'
            f'{e(tag)} <span style="color:#8a94a6;font-weight:400;">({len(events)})</span></h3>'
            f'{rows}</div>')
    resolved_block = ""
    resolved = payload.get("resolved") or []
    if resolved:
        rows = "".join(_event_row(ev) for ev in resolved[:20])
        resolved_block = (
            f'<div style="margin:20px 0;opacity:0.65;">'
            f'<h3 style="color:#389e0d;font-size:14px;margin:0 0 6px;">'
            f'已处理（{len(resolved)}）</h3>{rows}</div>')
    summary = payload.get("summary") or ""
    summary_block = ""
    if summary:
        summary_block = (
            f'<div style="background:#f0fdf4;border-left:4px solid #16a34a;'
            f'padding:12px 14px;border-radius:8px;font-size:14px;line-height:1.7;margin:14px 0;">'
            f'{e(summary)}</div>')
    since = payload.get("since") or "初次整理"
    kind = payload.get("kind") or "catchup"
    title = "每小时工作汇总" if kind == "hourly" else "积压整理"
    since_label = "最近一小时" if kind == "hourly" else f"自 {e(str(since)[:16])} 以来"
    return f"""<div style="font-family:-apple-system,'Microsoft YaHei',sans-serif;max-width:640px;margin:0 auto;color:#2c3e50;">
  <h2 style="color:#1a73e8;">📋 {title} · {e(payload.get('date',''))}</h2>
  <div style="font-size:12px;color:#8a94a6;margin-bottom:10px;">{since_label}</div>
  {summary_block}
  <div style="font-size:12px;color:#8a94a6;margin:12px 0;">待处理 {st.get('open',0)} · 分类 {st.get('groups',0)} · 已闭环 {st.get('resolved',0)}</div>
  {''.join(blocks) or '<div style="color:#999;">无待处理事项</div>'}
  {resolved_block}
  <hr style="margin-top:24px;border:none;border-top:1px solid #eee;">
  <div style="font-size:11px;color:#aaa;">由 MessageHub {'每小时汇总' if kind == 'hourly' else '唤醒补扫'}自动生成 · {e(payload.get('generated_at',''))}</div>
</div>"""


def render_urgent_html(payload: Dict[str, Any]) -> str:
    """加急邮件：单条事件，突出判定依据。"""
    from html import escape as e
    ev = payload.get("event", {})
    return f"""<div style="font-family:-apple-system,'Microsoft YaHei',sans-serif;max-width:640px;margin:0 auto;color:#2c3e50;">
  <h2 style="color:#d9381e;">⚡ 加急任务 · 需要你处理</h2>
  <div style="background:#fff7e6;border-left:4px solid #fa8c16;padding:12px 14px;border-radius:8px;margin:14px 0;">
    {_event_row(ev, show_tag=False)}
  </div>
  <div style="font-size:11px;color:#aaa;">由 MessageHub 加急分流自动发出 · {e(payload.get('generated_at',''))}</div>
</div>"""


def _send_html(subject: str, html_body: str) -> Dict[str, Any]:
    cfg = _smtp_config()
    if not cfg:
        return {"sent": False, "detail": "SMTP 未配置"}
    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = cfg["user"]
    msg["To"] = cfg["to"]
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        with smtplib.SMTP_SSL(cfg["host"], int(cfg["port"]), timeout=20, context=ssl.create_default_context()) as s:
            s.login(cfg["user"], cfg["pass"])
            s.sendmail(cfg["user"], [cfg["to"]], msg.as_string())
        return {"sent": True, "detail": "邮件已发送"}
    except Exception as e:
        print(f"[brief-mail] 发送失败: {type(e).__name__}: {e}")
        return {"sent": False, "detail": f"{type(e).__name__}: {e}"}


def send_digest_email(payload: Dict[str, Any]) -> Dict[str, Any]:
    st = payload.get("stats", {})
    if not st.get("open") and not st.get("resolved") and not st.get("review"):
        return {"sent": False, "detail": "空整理，静默"}
    kind = payload.get("kind") or "catchup"
    label = "每小时汇总" if kind == "hourly" else "积压整理"
    subject = (f"[MessageHub] {label} {payload.get('date','')} · "
               f"待办{st.get('open',0)} / {st.get('groups',0)}类")
    return _send_html(subject, render_digest_html(payload))


def send_urgent_email(payload: Dict[str, Any]) -> Dict[str, Any]:
    ev = payload.get("event", {})
    group = ev.get("conversation_name") or ""
    subject = f"[MessageHub] ⚡加急 · {group} · {(ev.get('text') or '')[:24]}"
    return _send_html(subject, render_urgent_html(payload))


def register() -> None:
    """把本模块注册为调度器通知钩子（幂等）。"""
    from messagehub.schedule import register_notify_hook
    register_notify_hook(send_brief_email)
