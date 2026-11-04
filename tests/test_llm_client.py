"""Тесты LLMClient (P2-08): Ollama без API-ключа работает, нет 'Bearer None'."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from kb_agent.config import AppConfig
from kb_agent.processing.llm_client import LLMClient


def _client(provider: str = "ollama", api_key: str | None = None) -> LLMClient:
    config = AppConfig(
        vault_path="/tmp/vault",
        data_dir="/tmp/data",
        db_path="/tmp/data/state.db",
        sources_path="/tmp/data/sources.yaml",
        profile_path="/tmp/data/user_profile.yaml",
        log_path="/tmp/data/kb_agent.log",
        llm_provider=provider,
        openai_api_key=api_key,
        openai_model="test-model",
    )
    return LLMClient(config)


class TestLLMClientNoKey:
    def test_ollama_without_key_does_not_refuse_upfront(self):
        """Ollama без ключа не возвращает ошибку 'API ключ не задан' при complete()."""
        client = _client(provider="ollama", api_key=None)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "ответ"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2},
        }
        mock_client = MagicMock()
        mock_client.__enter__.return_value.post.return_value = mock_resp
        mock_client.__exit__.return_value = False

        with patch("kb_agent.processing.llm_client.httpx.Client", return_value=mock_client):
            result = client.complete(
                messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "user"}]
            )

        assert result.error is None, f"не ожидалась ошибка, получено: {result.error}"
        assert result.text == "ответ"

    def test_headers_no_authorization_without_key(self):
        """Без ключа заголовок Authorization не посылается (нет 'Bearer None')."""
        client = _client(provider="ollama", api_key=None)
        assert "Authorization" not in client._headers()

    def test_headers_authorization_with_key(self):
        """С ключом заголовок Authorization посылается."""
        client = _client(provider="openai", api_key="sk-test12345678901234567890")
        assert client._headers()["Authorization"] == "Bearer sk-test12345678901234567890"

    def test_openai_without_key_refuses(self):
        """OpenAI без ключа отказывает (не локальный провайдер)."""
        client = _client(provider="openai", api_key=None)
        result = client.complete(
            messages=[{"role": "user", "content": "привет"}]
        )
        assert result.error == "API ключ LLM не задан"
        assert result.text == ""
