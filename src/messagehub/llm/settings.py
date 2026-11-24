"""AI 增强设置：工作台填写 -> runtime/llm_settings.json（跟随 DB_PATH，不入库）。

与 mail.py 的邮件设置同构，规则一致：
- 配置落在本机运行目录，不写仓库、不写源码；
- 公开接口只返回 has_key，绝不回传密钥明文；
-  blank 密钥表示保留原值，避免前端回显；
- 未写过 UI 配置时回退到 .env，兼容老用户。

导入顺序无关：这里像 mail.py 一样显式加载项目 .env。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict

try:
    from dotenv import load_dotenv

    _p = Path(__file__).resolve().parents[2] / ".env"
    if _p.exists() and os.environ.get("MESSAGEHUB_DISABLE_DOTENV") != "1":
        load_dotenv(str(_p), override=False)
except ImportError:
    pass

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
_settings_lock = threading.Lock()


def _settings_path() -> Path:
    from messagehub.config import DB_PATH

    return DB_PATH.parent / "llm_settings.json"


def _settings() -> Dict[str, Any]:
    try:
        data = json.loads(_settings_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, OSError, ValueError):
        return {}


def active_config() -> Dict[str, Any]:
    """返回实际生效配置；仅内部调用，密钥不得外传。"""
    cfg = _settings()
    api_key = cfg.get("api_key") or os.getenv("LLM_API_KEY") or ""
    return {
        "base_url": cfg.get("base_url") or os.getenv("LLM_BASE_URL", DEFAULT_BASE_URL),
        "model": cfg.get("model") or os.getenv("LLM_MODEL", DEFAULT_MODEL),
        "api_key": api_key,
        "has_key": bool(api_key),
        "enabled": bool(api_key) and cfg.get("enabled") is not False,
    }


def public_settings() -> Dict[str, Any]:
    """给前端的安全视图：不含 api_key。"""
    cfg = active_config()
    return {key: cfg[key] for key in ("base_url", "model", "enabled", "has_key")}


def save_settings(data: dict) -> None:
    if not isinstance(data, dict) or type(data.get("enabled")) is not bool:
        raise ValueError("请选择是否启用 AI 增强。")
    base_url = data.get("base_url", "")
    model = data.get("model", "")
    api_key = data.get("api_key", "")
    for value in (base_url, model, api_key):
        if not isinstance(value, str) or len(value) > 512 or any(c in value for c in "\r\n\x00"):
            raise ValueError("AI 配置格式无效。")
    base_url = base_url.strip().rstrip("/")
    model = model.strip()
    if not base_url or not model:
        raise ValueError("请填写接口地址和模型名。")
    if not (base_url.startswith("https://") or base_url.startswith("http://")):
        raise ValueError("接口地址需以 http:// 或 https:// 开头。")
    with _settings_lock:
        old = _settings()
        cfg = {
            "base_url": base_url,
            "model": model,
            "enabled": data["enabled"],
            "api_key": api_key.strip() or old.get("api_key", ""),
        }
        path = _settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=path.parent, delete=False) as handle:
            temp = Path(handle.name)
            try:
                json.dump(cfg, handle, ensure_ascii=False)
                handle.close()
                temp.replace(path)
            finally:
                temp.unlink(missing_ok=True)
