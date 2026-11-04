"""Тесты LLMClient (P2-08): Ollama без API-ключа работает, нет 'Bearer None'."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from kb_agent.config import AppConfig
from kb_agent.processing.llm_client import LLMClient, SummaryData


def _client(
    provider: str = "ollama",
    api_key: str | None = None,
    max_retries: int = 2,
) -> LLMClient:
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
        openai_max_retries=max_retries,
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


class TestLLMStructured:
    """P2-10: структурированный JSON-вывод, валидация темы, повтор при сбое схемы."""

    def _mock_client_post(self, client_mock, status_code=200, json_text=""):
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": json_text}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2},
        }
        mock_client = MagicMock()
        mock_client.__enter__.return_value.post.return_value = mock_resp
        mock_client.__exit__.return_value = False
        client_mock.return_value = mock_client

    def test_summarize_structured_valid_json(self):
        """Валидный JSON распаковывается в SummaryData с полями."""
        client = _client(provider="ollama", api_key=None)
        json_text = json.dumps(
            {
                "title": "Заголовок",
                "content": "# Заголовок\n\nСуть текста.",
                "tags": ["ии", "нейросети"],
                "topic": "ИИ и нейросети",
            }
        )
        with patch("kb_agent.processing.llm_client.httpx.Client") as mock_client:
            self._mock_client_post(mock_client, json_text=json_text)
            result = client.summarize_structured(
                raw_text="текст про нейросети",
                source_type="yt",
                source_name="yt_test",
                topics=["ИИ и нейросети", "Программирование"],
            )

        assert isinstance(result, SummaryData)
        assert result.title == "Заголовок"
        assert "Суть текста" in result.content
        assert result.tags == ["ии", "нейросети"]
        assert result.topic == "ИИ и нейросети"

    def test_summarize_structured_json_in_code_fence(self):
        """JSON внутри markdown-блока ```json ... ``` извлекается корректно."""
        client = _client(provider="ollama", api_key=None)
        json_text = "```json\n{\"title\": \"Т\", \"content\": \"C\", \"tags\": [], \"topic\": \"Общее\"}\n```"
        with patch("kb_agent.processing.llm_client.httpx.Client") as mock_client:
            self._mock_client_post(mock_client, json_text=json_text)
            result = client.summarize_structured(
                raw_text="текст", source_type="tg", source_name="tg_test", topics=[]
            )

        assert result.title == "Т"
        assert result.content == "C"
        assert result.topic == "Общее"

    def test_summarize_structured_retries_on_invalid_then_valid(self):
        """При первом невалидном ответе делается повтор и возвращается валидный."""
        client = _client(provider="ollama", api_key=None, max_retries=1)
        valid = json.dumps(
            {"title": "Ок", "content": "С", "tags": [], "topic": "Общее"}
        )
        with patch("kb_agent.processing.llm_client.httpx.Client") as mock_client:
            mock_client.return_value = self._flaky_client(valid)

            result = client.summarize_structured(
                raw_text="текст", source_type="tg", source_name="tg_test", topics=[]
            )

        assert result.title == "Ок"

    def _flaky_client(self, valid_json):
        """Клиент: первый вызов возвращает невалидный JSON, второй — валидный."""
        calls = {"n": 0}

        class _Resp:
            status_code = 200

            def json(self):
                calls["n"] += 1
                if calls["n"] == 1:
                    return {
                        "choices": [{"message": {"content": "не json"}}],
                        "usage": {},
                    }
                return {
                    "choices": [{"message": {"content": valid_json}}],
                    "usage": {},
                }

        mock_client = MagicMock()
        mock_client.__enter__.return_value.post.return_value = _Resp()
        mock_client.__exit__.return_value = False
        return mock_client

    def test_summarize_structured_all_invalid_fallback(self):
        """Если все ответы невалидны — возвращается заготовка с темой Общее."""
        client = _client(provider="ollama", api_key=None, max_retries=0)
        with patch("kb_agent.processing.llm_client.httpx.Client") as mock_client:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "choices": [{"message": {"content": "не json"}}],
                "usage": {},
            }
            mock_client = MagicMock()
            mock_client.__enter__.return_value.post.return_value = mock_resp
            mock_client.__exit__.return_value = False
            mock_client.return_value = mock_client

            result = client.summarize_structured(
                raw_text="текст", source_type="tg", source_name="tg_test", topics=[]
            )

        assert isinstance(result, SummaryData)
        assert result.topic == "Общее"
        assert result.content == ""

    def test_validate_topic_in_profile(self):
        """Тема из профиля возвращается как есть."""
        assert LLMClient._validate_topic("ИИ и нейросети", ["ИИ и нейросети", "Программирование"]) == "ИИ и нейросети"

    def test_validate_topic_closest_match(self):
        """Ближайшее совпадение (одна строка содержит другую) подбирается."""
        assert LLMClient._validate_topic("нейросети", ["ИИ и нейросети", "Программирование"]) == "ИИ и нейросети"

    def test_validate_topic_unknown(self):
        """Неизвестная тема → «Общее»."""
        assert LLMClient._validate_topic("Кулинария", ["ИИ и нейросети", "Программирование"]) == "Общее"

    def test_validate_topic_empty_profile(self):
        """Пустой профиль → тема как есть или Общее."""
        assert LLMClient._validate_topic("Любая", []) == "Любая"
        assert LLMClient._validate_topic("", []) == "Общее"

