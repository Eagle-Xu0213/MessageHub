"""微信 客户端 UIA 只读消息源。

**状态（2026-11-15 实测定案）：UIA 正文采集不可行，仅保留只读结构诊断。**
微信 4.2 消息区是 MMUIRenderSubWindowHW 自绘层，UIA 树里 0 个消息文本控件
（详见下方实测记录）。唯一在用的入口是 /api/live/probe 的结构诊断；
read() 保留完整边界逻辑，供未来客户端版本重新评估时复用。
实时通道一律走 POST /api/live/ingest。

边界（开发交接书 §1 / DECISIONS.md）：
- 只做 UI Automation 只读枚举：不点击、不置前、不发送、不改群设置。
- 只采集 allowed_chats 白名单内的会话；白名单为空时**只探测不返回消息**。
- 不解密、不读原始聊天数据库、不安装任何 Hook / 注入组件。
- UIA 拿不到精确消息时间时，写入采集时间并标记 time_confidence=capture_time，
  绝不伪称精确时间。

实测结论（2026-11-15，屏幕已解锁 + 用户授权所有群，本机复测两次）：
- 微信 4.2：顶层窗口 mmui::MainWindow 可见且非离屏，树内存在
  mmui::ChatMasterView / mmui::ChatDetailView / mmui::ChatMessagePage。
  但消息区由 MMUIRenderSubWindowHW 自绘：整棵树仅 63 个节点、
  0 个消息文本控件；ChatDetailView 的 TextPattern / ValuePattern 均不支持，
  LegacyIAccessible 名称与值为空；原生子窗口只有 2 个且无正文。
  → UIA 直读聊天正文在此客户端版本不可行，与解锁 / 白名单 / 窗口可见无关。
→ 因此实时接入以标准 HTTP 接入点（POST /api/live/ingest）为主通道。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

CN_TZ = timezone(timedelta(hours=8))


def _process_name(hwnd: int) -> str:
    """由窗口句柄反查进程名，避免把 Chrome/WorkBuddy 的 Chrome_WidgetWin_1 误判成 QQ。"""
    try:
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return ""
        buf = ctypes.create_unicode_buffer(512)
        size = wintypes.DWORD(512)
        ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
        ctypes.windll.kernel32.CloseHandle(handle)
        return buf.value.rsplit("\\", 1)[-1].lower()
    except Exception:
        return ""

WECHAT_WINDOW_CLASSES = ("mmui::MainWindow", "WeChatMainWnd", "Qt51514QWindowIcon")
WECHAT_MSG_CLASS_HINTS = ("ChatDetailView", "ChatMasterView", "msg", "Message")

# 明显的系统/非消息控件名，抓文本时排除
_SKIP_NAMES = re.compile(
    r"^(搜索|置顶|最小化|最大化|关闭|更多|手机|微信|通讯录|收藏|发现|导航|发送|表情|"
    r"输入框|聊天信息|语音|视频|文件|截图|滚动|Splitter|TitleBar)"
)
_NOISE = re.compile(r"^\s*$|^\[[^\]]{1,20}\]\s*$|^<sysmsg|^https?://\S+$")


def _now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def _stable_id(platform: str, chat: str, sender: str, text: str) -> str:
    """UIA 无平台消息 ID，用内容派生稳定 ID，保证重复抓取不重复入库。"""
    key = f"{platform}|{chat}|{sender}|{text}".encode("utf-8")
    return "uia::" + hashlib.sha256(key).hexdigest()[:20]


def _clean(text: str) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    return t[:800]


class UIASource:
    """只读 UIA 采集器。实例化不触碰窗口，read() 才枚举。"""

    def __init__(self, allowed_chats: Optional[List[str]] = None, max_messages: int = 40):
        self.allowed_chats = [c.strip() for c in (allowed_chats or []) if c.strip()]
        # "*" 表示用户已明确授权「所有群均可采集」，不再按群名逐个过滤
        self.capture_all = "*" in self.allowed_chats
        self.max_messages = max_messages
        self._ua = None

    def _lib(self):
        if self._ua is None:
            try:
                import uiautomation as ua
                self._ua = ua
            except ImportError:
                self._ua = False
        return self._ua

    def probe(self) -> Dict[str, Any]:
        """只读环境探测：列出客户端窗口与可读性状态，不返回任何聊天正文。"""
        ua = self._lib()
        if not ua:
            return {"available": False, "reason": "uiautomation 未安装",
                    "fix": "pip install uiautomation", "windows": []}
        try:
            root = ua.GetRootControl()
            wins = []
            for w in root.GetChildren():
                try:
                    cls, name = w.ClassName or "", w.Name or ""
                    hwnd = w.NativeWindowHandle
                except Exception:
                    continue
                proc = _process_name(hwnd)
                platform = None
                if "mmui" in cls or "WeChatMainWnd" in cls or proc in ("weixin.exe", "wechat.exe"):
                    platform = "wechat"
                if platform:
                    # 会话名是白名单匹配依据，不属于聊天正文，可以安全上报
                    chat_title = self._detect_chat_title(w)
                    wins.append({"platform": platform, "class": cls, "name": name,
                                 "process": proc, "handle": hwnd,
                                 "chat_title": chat_title,
                                 "offscreen": bool(w.IsOffscreen)})
            return {
                "available": True,
                "windows": wins,
                "wechat_window": any(x["platform"] == "wechat" for x in wins),
                "allowed_chats": self.allowed_chats,
                "capture_enabled": bool(self.allowed_chats),
                "capture_all": self.capture_all,
                "probed_at": _now_iso(),
            }
        except Exception as e:
            return {"available": False, "reason": f"{type(e).__name__}: {e}", "windows": []}

    def _find_chat_windows(self) -> List[Dict[str, Any]]:
        """找到可能承载聊天内容的窗口，返回 (platform, window, chat_name)。"""
        ua = self._lib()
        if not ua:
            return []
        found = []
        try:
            for w in ua.GetRootControl().GetChildren():
                try:
                    cls, name = w.ClassName or "", w.Name or ""
                    proc = _process_name(w.NativeWindowHandle)
                except Exception:
                    continue
                platform = None
                if "mmui" in cls or "WeChatMainWnd" in cls or proc in ("weixin.exe", "wechat.exe"):
                    platform = "wechat"
                if platform:
                    chat_name = self._detect_chat_title(w) or name
                    found.append({"platform": platform, "window": w,
                                  "chat_name": chat_name, "window_title": name,
                                  "process": proc, "class": cls})
        except Exception:
            pass
        return found

    @staticmethod
    def _detect_chat_title(win, max_depth: int = 7) -> str:
        """从控件树里找当前会话标题（微信主窗口标题固定是「微信」，不含群名）。"""
        hints = ("chat-header", "contact-name", "chattitle", "titleview",
                 "sessiontitle", "conversationtitle", "chatdetail")
        best = ""

        def walk(ctrl, depth):
            nonlocal best
            if depth > max_depth or best:
                return
            try:
                cls, name = (ctrl.ClassName or "").lower(), (ctrl.Name or "").strip()
            except Exception:
                return
            if any(h in cls for h in hints) and name and 1 < len(name) <= 60:
                if name not in ("微信", "Weixin", "WeChat", "导航"):
                    best = name
                    return
            try:
                children = ctrl.GetChildren()
            except Exception:
                return
            for ch in children:
                walk(ch, depth + 1)
                if best:
                    return

        try:
            walk(win, 0)
        except Exception:
            pass
        return best

    @staticmethod
    def _collect_text_controls(win, max_nodes: int = 400, max_depth: int = 8) -> List[Dict[str, str]]:
        """深度遍历控件树，收集有文本内容且像消息气泡的节点（正文脱敏前在这里产生）。"""
        out: List[Dict[str, str]] = []
        seen = set()

        def walk(ctrl, depth: int):
            if depth > max_depth or len(out) >= max_nodes:
                return
            try:
                ctype, cls, name = ctrl.ControlTypeName, ctrl.ClassName or "", ctrl.Name or ""
            except Exception:
                return
            text = _clean(name)
            if (text and len(text) >= 2 and not _NOISE.match(text)
                    and not _SKIP_NAMES.match(text) and text not in seen):
                is_bubble = any(h.lower() in cls.lower() for h in
                                WECHAT_MSG_CLASS_HINTS)
                if is_bubble or ctype in ("TextControl", "ListItemControl", "DataItemControl"):
                    seen.add(text)
                    out.append({"text": text, "class": cls, "type": ctype})
            try:
                children = ctrl.GetChildren()
            except Exception:
                return
            for ch in children:
                walk(ch, depth + 1)
                if len(out) >= max_nodes:
                    return

        try:
            walk(win, 0)
        except Exception:
            pass
        return out

    @staticmethod
    def _structure_report(win, max_nodes: int = 1200, max_depth: int = 12) -> Dict[str, Any]:
        """只读结构诊断：解释「为什么读不到正文」，不返回任何聊天内容。"""
        info = {"nodes": 0, "text_controls": 0, "bubble_like": 0,
                "self_rendered_layer": False, "truncated": False}

        def walk(ctrl, depth: int):
            if depth > max_depth or info["nodes"] >= max_nodes:
                return
            try:
                ctype, cls, name = ctrl.ControlTypeName, ctrl.ClassName or "", ctrl.Name or ""
            except Exception:
                return
            info["nodes"] += 1
            if "MMUIRenderSubWindowHW" in cls or "RenderSubWindow" in cls:
                info["self_rendered_layer"] = True
            if (len(name.strip()) >= 2 and not _NOISE.match(name)
                    and not _SKIP_NAMES.match(name.strip())):
                if ctype in ("TextControl", "ListItemControl", "DataItemControl"):
                    info["text_controls"] += 1
                if any(h.lower() in cls.lower() for h in
                       WECHAT_MSG_CLASS_HINTS):
                    info["bubble_like"] += 1
            try:
                children = ctrl.GetChildren()
            except Exception:
                return
            for ch in children:
                walk(ch, depth + 1)
                if info["nodes"] >= max_nodes:
                    info["truncated"] = True
                    return

        try:
            walk(win, 0)
        except Exception as e:
            info["error"] = f"{type(e).__name__}: {e}"
        return info

    def read(self) -> Dict[str, Any]:
        """抓取当前可见会话的消息。白名单为空时只返回探测状态，不返回消息。"""
        probe = self.probe()
        if not probe.get("available"):
            return {"status": "unavailable", "reason": probe.get("reason"),
                    "messages": [], "probe": probe, "structure": []}
        if not self.allowed_chats:
            return {
                "status": "needs_whitelist",
                "reason": "未授权采集范围：为遵守「不读未授权私聊」边界，暂不采集任何消息",
                "messages": [], "probe": probe, "structure": [],
            }
        if not probe["windows"]:
            return {"status": "no_window", "reason": "微信 主窗口不可见",
                    "messages": [], "probe": probe, "structure": []}

        all_msgs: List[Dict[str, Any]] = []
        details: List[Dict[str, Any]] = []
        structures: List[Dict[str, Any]] = []
        for info in self._find_chat_windows():
            chat = info["chat_name"] or ""
            # 微信主窗口标题固定为「微信」且 4.x 未在 UIA 暴露会话头，
            # 此时无法确知群名，用占位名并明确标记，绝不猜测。
            name_known = bool(chat) and chat not in ("微信", "Weixin", "WeChat")
            if not name_known:
                chat = f"{info['platform']}::当前可见会话"
            structures.append({"platform": info["platform"], "window": chat,
                               **self._structure_report(info["window"])})

            if self.capture_all:
                matched = chat
            else:
                matched = next((c for c in self.allowed_chats if c in chat), None)
            if not matched:
                details.append({"window": chat, "platform": info["platform"],
                                "skipped": "不在授权范围"})
                continue
            controls = self._collect_text_controls(info["window"])
            msgs = controls[-self.max_messages:]
            details.append({"window": chat, "platform": info["platform"],
                            "text_controls": len(msgs)})
            captured_at = _now_iso()
            for c in msgs:
                all_msgs.append({
                    "source_platform": info["platform"],
                    "source_method": "uia_readonly",
                    "conversation_id": f"uia::{info['platform']}::{matched}",
                    "conversation_name": matched,
                    "sender_name": "unknown",       # UIA 未稳定暴露发送者时不猜测
                    "sent_at": captured_at,          # 采集时间，非精确消息时间
                    "text": c["text"],
                    "message_type": "text",
                    "source_message_id": _stable_id(info["platform"], matched, "unknown", c["text"]),
                    "time_confidence": "capture_time",
                    "capture_status": "partial",
                    "conversation_confidence": "exact" if name_known else "unknown",
                })

        if not all_msgs:
            reason = "窗口存在但未读到消息文本；请打开目标群会话，且保持屏幕解锁"
            if any(s.get("self_rendered_layer") and not s.get("text_controls")
                   for s in structures):
                reason = (
                    "客户端消息区为自绘渲染层（MMUIRenderSubWindowHW）：UIA 控件树未暴露任何消息文本，"
                    "TextPattern / ValuePattern / 原生子窗口均无正文。这是客户端渲染架构限制，"
                    "解锁屏幕、窗口可见、放开全群授权都无法解决；"
                    "请改用标准接入点 POST /api/live/ingest 推送消息。"
                )
            return {
                "status": "no_visible_messages",
                "reason": reason,
                "messages": [], "probe": probe, "details": details,
                "structure": structures,
            }
        return {"status": "ok", "messages": all_msgs, "probe": probe,
                "details": details, "structure": structures}
