#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成「去年今日」离线回顾报告（纯静态 HTML，双击即可打开，不依赖服务器）。

用法：
    python scripts/build_memories_report.py            # 默认生成去年今日
    python scripts/build_memories_report.py 2025-11-14 # 指定日期
"""
from __future__ import annotations

import html
import json
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from messagehub.config import friendly_name as _friendly_name  # noqa: E402

DB_PATH = ROOT / "runtime" / "messagehub.db"
OUT_DIR = ROOT / "reports"


def friendly(cid: str, name: str) -> str:
    """群名映射统一走 config.friendly_name（别名在 config/personal.json）。"""
    return _friendly_name(cid, name)


def esc(v) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def target_date() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    today = date.today()
    try:
        return today.replace(year=today.year - 1).isoformat()
    except ValueError:  # 2/29
        return today.replace(year=today.year - 1, day=28).isoformat()


def fetch_messages(day: str):
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    start = f"{day}T00:00:00+08:00"
    end = f"{day}T23:59:59+08:00"
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM messages WHERE sent_at >= ? AND sent_at < ? ORDER BY sent_at ASC",
        [start, end],
    )]
    conn.close()
    for m in rows:
        m["conversation_name"] = friendly(
            str(m.get("conversation_id") or ""), str(m.get("conversation_name") or "")
        )
    return rows


def get_summary(messages, day: str, conversation: str):
    """优先读取已缓存的 AI 摘要，否则现场生成（失败自动降级规则摘要）。"""
    cache = OUT_DIR / f"ai_summary_{day}.json"
    if cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            print(f"  使用已缓存的 AI 摘要：{cache.name}")
            return data
        except Exception:
            pass

    from messagehub.llm.provider import LLMProvider
    print("  正在调用 AI 生成摘要（首次约需 10-30 秒）…")
    data = LLMProvider.from_env().summarize_memories(messages, conversation, day)
    if data:
        OUT_DIR.mkdir(exist_ok=True)
        cache.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  摘要已缓存到 {cache.name}")
    return data


def build(day: str) -> Path:
    OUT_DIR.mkdir(exist_ok=True)
    messages = fetch_messages(day)
    print(f"日期 {day}：读取到 {len(messages)} 条真实消息")

    if not messages:
        print("该日期无数据，仍将生成空报告")

    conv_counter = Counter(m["conversation_name"] for m in messages)
    main_conv = conv_counter.most_common(1)[0][0] if conv_counter else "全部群聊"
    senders = Counter(str(m.get("sender_name") or "未知") for m in messages)
    hours = Counter()
    for m in messages:
        ts = str(m.get("sent_at") or "")
        if len(ts) >= 13 and ts[11:13].isdigit():
            hours[int(ts[11:13])] += 1

    summary = get_summary(messages, day, main_conv) if messages else {}
    degraded = bool(summary.get("degraded", True)) if summary else True

    # ---- 统计卡片 ----
    stats_html = "".join([
        f'<div class="stat"><div class="num">{len(messages)}</div><div class="lbl">消息总数</div></div>',
        f'<div class="stat"><div class="num">{len(conv_counter)}</div><div class="lbl">活跃群聊</div></div>',
        f'<div class="stat"><div class="num">{len(senders)}</div><div class="lbl">参与成员</div></div>',
        f'<div class="stat"><div class="num">{sum(1 for m in messages if str(m.get("text") or "").strip() and not str(m.get("text") or "").startswith("[图片]"))}</div><div class="lbl">文字消息</div></div>',
    ])

    # ---- 成员排行 ----
    max_n = max(senders.values()) if senders else 1
    members_html = "".join(
        f'<div class="member"><span class="mname">{esc(n)}</span>'
        f'<span class="bar"><i style="width:{c / max_n * 100:.1f}%"></i></span>'
        f'<span class="mcount">{c}</span></div>'
        for n, c in senders.most_common(10)
    )

    # ---- 时段分布 ----
    peak = max(hours.values()) if hours else 1
    hours_html = "".join(
        f'<div class="hour" title="{h:02d}:00 · {hours.get(h, 0)} 条">'
        f'<i style="height:{max(3, hours.get(h, 0) / peak * 60):.0f}px"></i>'
        f'<span>{h:02d}</span></div>'
        for h in range(24)
    )

    # ---- AI 摘要 ----
    def ul(items, empty="暂无"):
        vals = items if isinstance(items, list) else []
        return "<ul>" + "".join(f"<li>{esc(x)}</li>" for x in vals) + "</ul>" if vals else f"<p class='muted'>{empty}</p>"

    banner = (
        '<div class="banner warn">AI 服务暂时不可用，以下为基于真实消息的本地规则摘要。</div>'
        if degraded else
        '<div class="banner ok">已由 AI 基于当天真实消息生成</div>'
    )
    summary_html = banner + f"""
        <div class="sec"><h3>🎯 那天主要在讨论什么</h3><p>{esc(summary.get('topic_summary') or '无概括')}</p></div>
        <div class="sec"><h3>✨ 值得回忆的事件</h3>{ul(summary.get('key_events'), '暂无记录')}</div>
        <div class="sec"><h3>💬 主要话题</h3>{ul(summary.get('main_topics'), '暂无话题')}</div>
        <div class="sec"><h3>🗨️ 代表性发言</h3><div class="quotes">{ul(summary.get('representative_quotes'), '暂无代表性发言')}</div></div>
        <div class="sec"><h3>🌟 整体氛围</h3><p>{esc(summary.get('atmosphere') or '未评估')}</p></div>
        <div class="foot">ℹ️ {esc(summary.get('source_note') or '基于数据库真实消息生成')}</div>
    """

    # ---- 完整消息时间线 ----
    msg_html = []
    last_hour = None
    for m in messages:
        ts = str(m.get("sent_at") or "")
        hour = ts[11:13] if len(ts) >= 13 else "??"
        if hour != last_hour:
            msg_html.append(f'<div class="hour-sep">{hour}:00</div>')
            last_hour = hour
        text = str(m.get("text") or "")
        is_media = text.startswith("[") and text.endswith("]") and len(text) < 20
        cls = "media" if is_media else ""
        msg_html.append(
            f'<div class="msg"><div class="mhead"><b>{esc(m.get("sender_name"))}</b>'
            f'<span>{esc(ts[11:19] if len(ts) >= 19 else ts)}</span></div>'
            f'<div class="mtext {cls}">{esc(text)}</div></div>'
        )
    timeline = "".join(msg_html) or '<div class="empty">该日期无消息记录</div>'

    groups_html = "".join(
        f'<div class="group"><span>{esc(n)}</span><b>{c} 条</b></div>'
        for n, c in conv_counter.most_common()
    )

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>去年今日 · {esc(day)} · MessageHub</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:system-ui,-apple-system,"Microsoft YaHei",sans-serif;background:#f4f5f8;color:#2c3444;line-height:1.7}}
.wrap{{max-width:1000px;margin:0 auto;padding:28px 20px 80px}}
header{{background:linear-gradient(135deg,#4f46e5,#7c3aed);color:#fff;padding:34px;border-radius:16px;margin-bottom:22px;box-shadow:0 8px 24px rgba(79,70,229,.22)}}
header h1{{font-size:28px;margin-bottom:6px}}
header .date{{font-size:18px;opacity:.92}}
header .sub{{font-size:13px;opacity:.75;margin-top:10px}}
.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin-bottom:22px}}
.stat{{background:#fff;padding:18px;border-radius:12px;text-align:center;box-shadow:0 2px 8px rgba(0,0,0,.05)}}
.num{{font-size:30px;font-weight:700;color:#4f46e5}}
.lbl{{font-size:12px;color:#8a94a6}}
.card{{background:#fff;padding:24px 28px;border-radius:12px;margin-bottom:20px;box-shadow:0 2px 8px rgba(0,0,0,.05)}}
.card h2{{font-size:18px;margin-bottom:16px;padding-bottom:10px;border-bottom:2px solid #f0f1f5}}
.banner{{padding:11px 14px;border-radius:8px;font-size:13px;margin-bottom:18px}}
.banner.ok{{background:#f0fdf4;border-left:4px solid #16a34a;color:#166534}}
.banner.warn{{background:#fff7e6;border-left:4px solid #f59e0b;color:#92400e}}
.sec{{margin-bottom:18px}}
.sec h3{{font-size:14px;margin-bottom:7px;color:#4338ca}}
.sec p,.sec li{{font-size:14px;color:#404a5c}}
.sec ul{{padding-left:20px}}
.sec li{{margin:5px 0}}
.quotes li{{font-style:italic;color:#6b7280}}
.foot{{margin-top:18px;padding:10px;background:#f7f8fa;border-radius:6px;font-size:12px;color:#98a1b3}}
.muted{{color:#98a1b3;font-size:14px}}
.member{{display:flex;align-items:center;gap:12px;margin:8px 0;font-size:13px}}
.mname{{width:120px;flex-shrink:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.bar{{flex:1;background:#eef0f6;height:10px;border-radius:6px;overflow:hidden}}
.bar i{{display:block;height:100%;background:linear-gradient(90deg,#6366f1,#a855f7);border-radius:6px}}
.mcount{{width:44px;text-align:right;color:#8a94a6}}
.chart{{display:flex;align-items:flex-end;gap:5px;height:80px;margin-top:10px}}
.hour{{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;gap:4px}}
.hour i{{display:block;width:100%;background:linear-gradient(180deg,#818cf8,#4f46e5);border-radius:3px 3px 0 0;min-height:3px}}
.hour span{{font-size:9px;color:#a0a8b8}}
.group{{display:flex;justify-content:space-between;padding:9px 0;border-bottom:1px solid #f0f1f5;font-size:14px}}
.group:last-child{{border-bottom:none}}
.timeline{{max-height:640px;overflow-y:auto;border:1px solid #eef0f4;border-radius:8px;padding:0 16px}}
.hour-sep{{position:sticky;top:0;background:#f8f9fc;color:#8a94a6;font-size:12px;padding:6px 10px;margin:8px -16px;border-radius:4px;z-index:1}}
.msg{{padding:9px 0;border-bottom:1px solid #f4f5f8}}
.mhead{{display:flex;justify-content:space-between;font-size:12px;margin-bottom:3px}}
.mhead b{{color:#4f46e5}}
.mhead span{{color:#a0a8b8}}
.mtext{{font-size:14px;word-break:break-word;white-space:pre-wrap}}
.mtext.media{{color:#a0a8b8;font-size:12px}}
.empty{{text-align:center;padding:40px;color:#98a1b3}}
footer{{text-align:center;color:#a0a8b8;font-size:12px;margin-top:28px}}
@media print{{body{{background:#fff}}.card,header,.stat{{box-shadow:none}}.timeline{{max-height:none;overflow:visible}}}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>📅 去年今日回顾</h1>
  <div class="date">{esc(day)}</div>
  <div class="sub">数据来源：MessageHub 本地归档 · 群聊「{esc(main_conv)}」 · 按北京时间</div>
</header>

<div class="stats">{stats_html}</div>

<div class="card">
  <h2>🤖 AI 回顾总结</h2>
  {summary_html}
</div>

<div class="card">
  <h2>👥 当日活跃成员</h2>
  {members_html or '<p class="muted">无数据</p>'}
</div>

<div class="card">
  <h2>🕐 全天消息分布</h2>
  <div class="chart">{hours_html}</div>
  <p class="muted" style="font-size:12px;margin-top:10px">横轴为小时（00–23），柱高代表该时段消息量</p>
</div>

<div class="card">
  <h2>💬 群聊分布</h2>
  {groups_html or '<p class="muted">无数据</p>'}
</div>

<div class="card">
  <h2>📜 完整聊天时间线（{len(messages)} 条）</h2>
  <div class="timeline">{timeline}</div>
</div>

<footer>由 MessageHub 生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')} · 本文件为静态快照，可离线查看 / 分享 / 打印为 PDF</footer>
</div>
</body>
</html>"""

    out = OUT_DIR / f"去年今日_{day}.html"
    out.write_text(doc, encoding="utf-8")
    print(f"✅ 报告已生成：{out}")
    return out


if __name__ == "__main__":
    build(target_date())
