"""简报邮件桥：把每日简报以 HTML 邮件发送到指定收件箱。

为什么用邮件：没有具体时间的事件不适合塞进日历，而邮件能被各种
AI 助手读取并统一规划日程——简报发到邮箱，后续处理交给读邮件的工具。

配置（.env，全部可选；不配则只在网页显示简报，不发信）：
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
import os
import smtplib
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, Optional

# 显式加载项目 .env（工作目录无关）
try:
    from dotenv import load_dotenv
    _p = Path(__file__).resolve().parents[2] / ".env"
    if _p.exists():
        load_dotenv(str(_p), override=False)
except ImportError:
    pass


def _smtp_config() -> Optional[Dict[str, str]]:
    cfg = {
        "host": os.getenv("SMTP_HOST", "smtp.qq.com"),
        "port": os.getenv("SMTP_PORT", "465"),
        "user": os.getenv("SMTP_USER", ""),
        "pass": os.getenv("SMTP_PASS", ""),
        "to": os.getenv("BRIEF_TO", ""),
    }
    if not (cfg["user"] and cfg["pass"] and cfg["to"]):
        return None
    return cfg


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
        with smtplib.SMTP_SSL(cfg["host"], int(cfg["port"]), timeout=20) as s:
            s.login(cfg["user"], cfg["pass"])
            s.sendmail(cfg["user"], [cfg["to"]], msg.as_string())
        print(f"[brief-mail] 已发送 {subject} → {cfg['to']}")
        return {"sent": True, "detail": f"{subject} → {cfg['to']}"}
    except Exception as e:
        print(f"[brief-mail] 发送失败: {type(e).__name__}: {e}")
        return {"sent": False, "detail": f"{type(e).__name__}: {e}"}


def register() -> None:
    """把本模块注册为调度器通知钩子（幂等）。"""
    from messagehub.schedule import register_notify_hook
    register_notify_hook(send_brief_email)
