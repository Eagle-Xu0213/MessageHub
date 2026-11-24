"""Windows 桌面通知（toast）：紧急待办即时可见。

不引入 win11toast / plyer 等第三方依赖，直接调 PowerShell 的
Windows.UI.Notifications API（Win10/11 内置）。失败静默返回 False，
不影响邮件主通道。仅在 Windows 上生效。
"""
from __future__ import annotations

import html
import os
import subprocess
import sys
from typing import Any, Dict

# 应用标识：让通知在系统通知中心显示固定名称
_APP_ID = "MessageHub"
_PS_TEMPLATE = r'''
$ErrorActionPreference = 'Stop'
[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
$appId = __APPID__
$toastType = [Windows.UI.Notifications.ToastTemplateType, Windows.UI.Notifications, ContentType = WindowsRuntime]
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($toastType::ToastText02)
$texts = $template.GetElementsByTagName("text")
$texts.Item(0).AppendChild($template.CreateTextNode(__TITLE__)) | Out-Null
$texts.Item(1).AppendChild($template.CreateTextNode(__BODY__)) | Out-Null
$node = $template.SelectSingleNode('/toast')
$node.SetAttribute('activationType', 'protocol')
$node.SetAttribute('launch', __URL__)
$toast = New-Object Windows.UI.Notifications.ToastNotification($template)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
'''
_BALLOON_TEMPLATE = r'''
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$icon = New-Object System.Windows.Forms.NotifyIcon
$icon.Icon = [System.Drawing.SystemIcons]::Information
$icon.Visible = $true
$icon.BalloonTipTitle = __TITLE__
$icon.BalloonTipText = __BODY__
$url = __URL__
$icon.add_BalloonTipClicked({ Start-Process $url })
$icon.ShowBalloonTip(10000)
$until = [DateTime]::UtcNow.AddSeconds(12)
while ([DateTime]::UtcNow -lt $until) {
    [System.Windows.Forms.Application]::DoEvents()
    Start-Sleep -Milliseconds 200
}
$icon.Visible = $false
$icon.Dispose()
'''


def _ps_string(s: str) -> str:
    """转成 PowerShell 单引号字符串字面量（内部单引号翻倍）。"""
    return "'" + (s or "").replace("'", "''") + "'"


def notify(title: str, body: str, app_id: str = _APP_ID, url: str = "http://127.0.0.1:8000/") -> bool:
    """发一条桌面通知。成功 True，任何失败 False（不抛异常）。"""
    if sys.platform != "win32":
        return False
    script = (_PS_TEMPLATE
              .replace("__TITLE__", _ps_string(title[:60]))
              .replace("__BODY__", _ps_string(body[:200]))
              .replace("__URL__", _ps_string(url))
              .replace("__APPID__", _ps_string(app_id)))
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, timeout=12, creationflags=creationflags)
        if proc.returncode == 0:
            return True
        fallback = (_BALLOON_TEMPLATE.replace("__TITLE__", _ps_string(title[:60]))
                    .replace("__BODY__", _ps_string(body[:200]))
                    .replace("__URL__", _ps_string(url)))
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", fallback],
            capture_output=True, timeout=20, creationflags=creationflags)
        return proc.returncode == 0
    except Exception:
        return False


def notify_urgent(event: Dict[str, Any]) -> bool:
    """加急事件专用通知。"""
    group = event.get("conversation_name") or "未知会话"
    text = (event.get("text") or "").strip()[:120]
    ddl = event.get("deadline") or ""
    body = text + (f"\n截止：{ddl}" if ddl else "")
    task_id = event.get("id")
    url = "http://127.0.0.1:8000/" + (f"#task-{int(task_id)}" if task_id is not None else "")
    return notify("⚡ 紧急待办 · " + group, body, url=url)
