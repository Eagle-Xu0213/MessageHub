"""工作台 AI 设置：本地落盘、密钥不外泄、与 LLMProvider 联动。"""
from __future__ import annotations

import json

import pytest

from messagehub.llm import settings as ai_settings
from messagehub.llm.provider import LLMProvider


@pytest.fixture
def settings_path(tmp_path, monkeypatch):
    path = tmp_path / "llm_settings.json"
    monkeypatch.setattr(ai_settings, "_settings_path", lambda: path)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    return path


def _payload(**overrides):
    data = {"enabled": True, "base_url": "https://api.example.invalid/v1",
            "model": "fixture-model", "api_key": "fixture-key-001"}
    data.update(overrides)
    return data


def test_llm_settings_keep_key_local_and_hide_it_from_public_view(settings_path):
    ai_settings.save_settings(_payload())
    stored = json.loads(settings_path.read_text(encoding="utf-8"))
    assert stored["api_key"] == "fixture-key-001"

    view = ai_settings.public_settings()
    assert view["has_key"] is True
    assert view["base_url"] == "https://api.example.invalid/v1"
    assert "fixture-key-001" not in str(view)

    # 前端不回显密钥：留空表示保留原值
    ai_settings.save_settings(_payload(api_key="", enabled=False))
    assert ai_settings.active_config()["api_key"] == "fixture-key-001"
    assert ai_settings.public_settings()["enabled"] is False


def test_llm_settings_reject_malformed_values(settings_path):
    with pytest.raises(ValueError):
        ai_settings.save_settings(_payload(enabled="yes"))
    with pytest.raises(ValueError):
        ai_settings.save_settings(_payload(base_url="ftp://api.example.invalid"))
    with pytest.raises(ValueError):
        ai_settings.save_settings(_payload(model=""))
    with pytest.raises(ValueError):
        ai_settings.save_settings(_payload(api_key="bad\nkey"))
    assert not settings_path.exists()


def test_provider_from_env_uses_workbench_settings(settings_path):
    ai_settings.save_settings(_payload())
    provider = LLMProvider.from_env()
    assert provider.enabled is True
    assert provider.base_url == "https://api.example.invalid/v1"
    assert provider.model == "fixture-model"
    assert provider.api_key == "fixture-key-001"

    ai_settings.save_settings(_payload(api_key="", enabled=False))
    assert LLMProvider.from_env().enabled is False


def test_llm_settings_api_is_local_only_and_never_returns_key(settings_path):
    from fastapi.testclient import TestClient

    from messagehub.app import app

    client = TestClient(app, base_url="http://localhost:8000")
    headers = {"Origin": "http://localhost:8000"}
    url = "/api/settings/llm"

    assert client.put(url, json=_payload(),
                      headers={"Origin": "http://other.invalid"}).status_code == 403
    saved = client.put(url, json=_payload(), headers=headers)
    assert saved.status_code == 200
    assert saved.json()["has_key"] is True
    assert "fixture-key-001" not in saved.text
    assert "fixture-key-001" not in client.get(url).text

    assert client.put(url, json=_payload(base_url="ftp://bad"), headers=headers).status_code == 400
