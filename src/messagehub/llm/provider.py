"""可选 LLM 提供者：OpenAI Chat Completions 兼容接口。

- 默认使用免费的 LLM7.io，无需密钥。
- 总结功能具备完整降级链：多模型重试 → JSON 稳健解析 → 本地规则摘要。
- 任何失败都不影响主流程。
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Optional, List, Dict, Any

# 显式定位项目根 .env，避免工作目录不同导致读不到配置
try:
    from dotenv import load_dotenv

    _env_candidates = [
        Path(__file__).resolve().parents[3] / ".env",
        Path.cwd() / ".env",
    ]
    for _p in _env_candidates:
        if _p.exists():
            load_dotenv(str(_p), override=True)
            break
    else:
        load_dotenv()
except ImportError:
    pass


def _extract_json(content: str) -> Optional[dict]:
    """从 LLM 输出中稳健提取 JSON：兼容纯 JSON、markdown 代码块、前后解释文字。"""
    if not content or not content.strip():
        return None
    text = content.strip()

    candidates = []
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fenced:
        candidates.append(fenced.group(1).strip())
    first, last = text.find("{"), text.rfind("}")
    if first != -1 and last > first:
        candidates.append(text[first:last + 1])
    candidates.append(text)

    for cand in candidates:
        try:
            data = json.loads(cand)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, TypeError):
            continue
    return None


def _rule_based_summary(messages: List[Dict[str, Any]], conversation: str,
                        date: str) -> Dict[str, Any]:
    """LLM 完全不可用时的本地规则降级摘要，保证功能永远有输出。"""
    texts = [str(m.get("text") or "").strip() for m in messages]
    meaningful = [
        t for t in texts
        if t and not re.fullmatch(r"\[[^\]]{1,12}\]", t) and len(t) >= 2
    ]

    senders = Counter(str(m.get("sender_name") or "未知") for m in messages)
    top_senders = [f"{name}（{cnt} 条）" for name, cnt in senders.most_common(5)]

    hours = Counter()
    for m in messages:
        mt = re.search(r"T(\d{2}):", str(m.get("sent_at") or ""))
        if mt:
            hours[int(mt.group(1))] += 1
    peak_hours = hours.most_common(3)

    quotes = sorted(
        [t for t in meaningful if re.search(r"[\u4e00-\u9fff]", t) and len(t) >= 8],
        key=len, reverse=True,
    )[:5]

    words = Counter()
    for t in meaningful:
        for w in re.findall(r"[\u4e00-\u9fff]{2,6}", t):
            words[w] += 1
    topics = [w for w, cnt in words.most_common(8) if cnt >= 2][:5]

    time_desc = ""
    if peak_hours:
        time_desc = "、".join(f"{h:02d}:00" for h, _ in peak_hours) + " 前后最活跃"

    return {
        "topic_summary": (
            f"{date} 当天「{conversation}」共产生 {len(messages)} 条消息，"
            f"{len(senders)} 位成员参与，{time_desc or '全天均有交流'}。"
        ),
        "key_events": [
            f"全天累计 {len(messages)} 条消息，其中 {len(meaningful)} 条为文字内容",
            f"最活跃成员：{'、'.join(top_senders[:3]) or '暂无'}",
            f"交流高峰时段：{time_desc or '分布较均匀'}",
        ],
        "main_topics": topics or ["日常交流"],
        "representative_quotes": quotes or ["（当天多为图片、表情等短消息）"],
        "atmosphere": "轻松活跃的群聊日常",
        "message_count": len(messages),
        "source_note": "基于数据库真实消息生成（本地规则降级摘要）",
        "degraded": True,
    }


class LLMProvider:
    """OpenAI 兼容的结构化任务提取 / 回顾总结接口。"""

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None,
                 model: Optional[str] = None, enabled: bool = False):
        self.base_url = base_url or os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
        self.api_key = api_key or os.getenv("LLM_API_KEY")
        self.model = model or os.getenv("LLM_MODEL", "gpt-4o")
        self.enabled = enabled and bool(self.api_key)

    @classmethod
    def from_env(cls) -> "LLMProvider":
        return cls(enabled=bool(os.getenv("LLM_API_KEY")))

    # ---------------- 统一降级链 ----------------

    def _attempts(self) -> List[Dict[str, Any]]:
        """候选调用序列：主服务主模型 → 免费备用 → 主服务备模型。

        429 表示配额耗尽，立即切换下一个，避免长时间等待。
        """
        url = self._endpoint()
        headers = {"Authorization": f"Bearer {self.api_key}"}
        attempts = [
            (url, headers, self.model, 0.2),
            ("https://text.pollinations.ai/openai", {}, "openai", 0.2),
            ("https://api.llm7.io/v1",
             {"Authorization": "Bearer unused"}, "gpt-oss:20b", 0.2),
            (url, headers, "DeepSeek-V4-Flash-0731", 0.2),
        ]
        seen = set()
        out = []
        for ep, hdr, mdl, temp in attempts:
            if not mdl or (mdl + ep) in seen:
                continue
            seen.add(mdl + ep)
            out.append((ep, hdr, mdl, temp))
        return out

    def chat_json(self, prompt: str, timeout: float = 60.0,
                  temperature: float = 0.2, retries: int = 2) -> Optional[dict]:
        """按降级链请求一次 JSON 输出；全部失败返回 None。

        这是全项目唯一的 LLM JSON 调用入口（work_brief 等模块不得自建副本）。
        免费服务限流有随机性（实测 3 连败后重试常成功），默认整条链重试 2 轮。
        """
        if not self.enabled:
            return None
        try:
            import httpx
        except ImportError:
            return None

        for round_no in range(max(1, retries + 1)):
            if round_no:
                import time as _time
                _time.sleep(2 * round_no)  # 限流退避：2s、4s
            for ep, hdr, model, temp in self._attempts():
                try:
                    with httpx.Client(timeout=timeout) as c:
                        r = c.post(
                            ep, headers=hdr,
                            json={"model": model,
                                  "messages": [{"role": "user", "content": prompt}],
                                  "temperature": temp},
                        )
                        if r.status_code == 429:
                            print(f"[LLM] {model} 配额/限流，切换到下一个服务")
                            continue
                        r.raise_for_status()
                        choice = r.json()["choices"][0]["message"]
                        content = choice.get("content") or choice.get("reasoning_content") or ""

                    data = _extract_json(content)
                    if not data:
                        print(f"[LLM] 模型 {model} 输出无法解析为 JSON：{content[:120]!r}")
                        continue
                    return data
                except Exception as e:
                    print(f"[LLM] 模型 {model} 调用失败：{type(e).__name__}: {e}")
        return None

    def _endpoint(self) -> str:
        """兼容 base_url 已包含 /chat/completions 的情况。"""
        base = self.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return base + "/chat/completions"

    def extract(self, text: str, sender: str, conversation: str,
                self_names: list[str]) -> Optional[dict]:
        """调用 LLM 提取结构化任务；失败返回 None 让调用方回退规则。"""
        if not self.enabled:
            return None
        try:
            import httpx
        except ImportError:
            return None

        prompt = (
            "你是消息整理助手。从一条聊天消息中提取工作事项，返回严格 JSON：\n"
            '{"title": 简短标题, "assignee": 负责人或null, "my_role": '
            '"我需要执行"|"我需要监督"|"其他人的任务"|"待确认", '
            '"deadline": 截止时间文本或null, "category": '
            '"urgent_action"|"action"|"delegated_followup"|"waiting"|'
            '"reply_needed"|"info_only"|"noise"|"uncertain", '
            '"requires_reply": bool}\n'
            f"我的昵称可能是：{','.join(self_names) or '未知'}。\n"
            f"群聊：{conversation}，发送者：{sender}\n消息：{text}\n"
            "只返回 JSON，不要解释。"
        )
        try:
            with httpx.Client(timeout=15.0) as c:
                r = c.post(
                    self._endpoint(),
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.0,
                    },
                )
                r.raise_for_status()
                data = _extract_json(r.json()["choices"][0]["message"]["content"])
            if not data:
                return None
            role_map = {
                "我需要执行": "我需要执行", "我需要监督": "我需要监督",
                "其他人的任务": "其他人的任务", "待确认": "待确认",
            }
            cat_map = {"urgent_action", "action", "delegated_followup", "waiting",
                       "reply_needed", "info_only", "noise", "uncertain"}
            return {
                "title": str(data.get("title", text[:40])),
                "description": text,
                "assignee": data.get("assignee"),
                "my_role": role_map.get(data.get("my_role"), "待确认"),
                "deadline": data.get("deadline"),
                "category": data.get("category") if data.get("category") in cat_map else "uncertain",
                "requires_reply": bool(data.get("requires_reply", False)),
                "reply_suggestion": None,
                "source_conversation": conversation,
                "source_sender": sender,
                "status": "uncertain",
            }
        except Exception:
            return None

    def summarize_memories(
        self,
        messages: List[Dict[str, Any]],
        conversation: str,
        date: str
    ) -> Optional[Dict[str, Any]]:
        """生成「去年今日」回顾摘要。

        健壮性：兼容推理模型空 content、markdown 代码块 JSON、429 限流退避；
        LLM 彻底不可用时降级为本地规则摘要，保证永远有输出。
        """
        fallback = _rule_based_summary(messages, conversation, date)
        if not self.enabled:
            return fallback

        try:
            import httpx
        except ImportError:
            return fallback

        lines = []
        for msg in messages:
            sender = str(msg.get("sender_name") or "未知")
            text = str(msg.get("text") or "").strip()
            if text:
                lines.append(f"[{sender}]: {text}")
        text_sample = "\n".join(lines)
        if len(text_sample) > 16000:
            text_sample = text_sample[:16000] + "\n...（后续消息略）"

        prompt = (
            f"你是 AI 聊天回顾助手。根据以下真实聊天记录，为 '{conversation}' 在 '{date}' 这一天生成结构化总结。\n\n"
            f"聊天记录：\n{text_sample}\n\n"
            "严格返回 JSON 对象（不要 markdown 代码块、不要解释文字）：\n"
            '{"topic_summary":"一句话概括当天主要讨论内容",'
            '"key_events":["值得回忆的事件1","事件2","事件3"],'
            '"main_topics":["话题1","话题2","话题3"],'
            '"representative_quotes":["原文摘录1","原文摘录2","原文摘录3"],'
            '"atmosphere":"整体氛围描述",'
            '"message_count":' + str(len(messages)) + ','
            '"source_note":"基于数据库真实消息生成"}\n\n'
            "要求：只依据真实消息，不要编造；引用原话必须是聊天记录里出现过的句子。"
        )

        data = self.chat_json(prompt, timeout=90.0)
        if data:
            data.setdefault("message_count", len(messages))
            data.setdefault("source_note", "基于数据库真实消息生成")
            data["degraded"] = False
            return data

        print("[LLM] 所有模型均失败，使用本地规则降级摘要")
        return fallback

    def generate_overview(
        self,
        group_summaries: List[Dict[str, Any]],
        total_date: str
    ) -> Optional[Dict[str, Any]]:
        """生成跨群的「去年今日总览」。"""
        if not self.enabled:
            return None
        try:
            import httpx
        except ImportError:
            return None

        overview_text = "\n\n".join([
            f"【{g.get('conversation_name', '未知群')}】\n主题：{g.get('topic_summary', '无')}\n"
            f"事件：{', '.join(g.get('key_events', []))}\n话题：{', '.join(g.get('main_topics', []))}"
            for g in group_summaries
        ])
        prompt = (
            f"以下是 {total_date} 这天所有群聊的回顾摘要：\n{overview_text}\n\n"
            "请生成一个跨群的总体概览，返回 JSON：\n"
            '{"overall_topic":"当天的整体讨论主题",'
            '"highlight_groups":["重点参与的群聊"],'
            '"common_themes":["共同话题"],'
            '"cross_group_events":["跨群聊的事件或讨论"],'
            '"total_groups":' + str(len(group_summaries)) + ','
            '"source_note":"基于多个群聊的真实消息汇总生成"}\n\n'
            "注意：不编造任何未提及的内容。"
        )
        try:
            with httpx.Client(timeout=30.0) as c:
                r = c.post(
                    self._endpoint(),
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.3,
                    },
                )
                r.raise_for_status()
                return _extract_json(r.json()["choices"][0]["message"]["content"])
        except Exception as e:
            print(f"LLM 总览生成失败：{e}")
            return None
