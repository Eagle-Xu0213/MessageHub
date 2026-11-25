"""可选 LLM 提供者：OpenAI Chat Completions 兼容接口。

- 配置 API 密钥后启用；部分请求失败时会尝试第三方备用服务。
- 结构化请求支持多模型重试和 JSON 解析。
- 任何失败都不影响主流程。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional, List, Dict, Any
from urllib.parse import urlsplit

# 显式定位项目根 .env，避免工作目录不同导致读不到配置
try:
    from dotenv import load_dotenv

    _env_candidates = [
        Path(__file__).resolve().parents[3] / ".env",
        Path.cwd() / ".env",
    ]
    for _p in ([] if os.environ.get('MESSAGEHUB_DISABLE_DOTENV') == '1' else _env_candidates):
        if _p.exists():
            load_dotenv(str(_p), override=True)
            break
    else:
        if os.environ.get('MESSAGEHUB_DISABLE_DOTENV') != '1':
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


class LLMProvider:
    """OpenAI 兼容的结构化任务提取接口。"""

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None,
                 model: Optional[str] = None, enabled: bool = False):
        self.base_url = base_url or os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
        self.api_key = api_key or os.getenv("LLM_API_KEY")
        self.model = model or os.getenv("LLM_MODEL", "gpt-4o")
        self.enabled = enabled and bool(self.api_key)

    @classmethod
    def from_env(cls) -> "LLMProvider":
        """工作台配置优先，未配置时回退到 .env（兼容老用户）。"""
        from messagehub.llm.settings import active_config

        cfg = active_config()
        return cls(base_url=cfg["base_url"], api_key=cfg["api_key"] or None,
                   model=cfg["model"], enabled=cfg["enabled"])

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
                  temperature: float = 0.2, retries: int = 2, *,
                  primary_only: bool = False) -> Optional[dict]:
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

        attempts = ([(self._endpoint(), {"Authorization": f"Bearer {self.api_key}"},
                      self.model, temperature)] if primary_only else self._attempts())
        for round_no in range(max(1, retries + 1)):
            if round_no:
                import time as _time
                _time.sleep(2 * round_no)  # 限流退避：2s、4s
            for ep, hdr, model, temp in attempts:
                try:
                    local_primary = primary_only and urlsplit(ep).hostname in ("127.0.0.1", "localhost", "::1")
                    with httpx.Client(timeout=timeout, trust_env=not local_primary) as c:
                        r = c.post(
                            ep, headers=hdr,
                            json={"model": model,
                                  "messages": [{"role": "user", "content": prompt}],
                                  "temperature": temp},
                        )
                        if r.status_code == 429 and not primary_only:
                            print(f"[LLM] {model} 配额/限流，切换到下一个服务")
                            continue
                        r.raise_for_status()
                        choice = r.json()["choices"][0]["message"]
                        content = choice.get("content") or choice.get("reasoning_content") or ""

                    data = _extract_json(content)
                    if not data:
                        if not primary_only:
                            print(f"[LLM] 模型 {model} 输出无法解析为 JSON：{content[:120]!r}")
                        continue
                    return data
                except Exception as e:
                    if not primary_only:
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
