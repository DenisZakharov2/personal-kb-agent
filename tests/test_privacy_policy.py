"""Тесты политики приватности (6.2) — поведение, а не чтение конфига.

Проверяется, что заявленные в документации настройки реально работают:

- ``KB_LOCAL_PROCESSING_MODE=true`` блокирует запросы к внешнему LLM;
- ``KB_REDACT_FIELDS`` вырезает чувствительные значения из текста запроса;
- ``KB_REDACT_SOURCES`` исключает контент источника из отправки во внешний LLM;
- локальные провайдеры работают без ограничений (данные не покидают машину).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from kb_agent.config import AppConfig
from kb_agent.core.search import SearchEngine
from kb_agent.processing.llm_client import LLMClient
from kb_agent.processing.privacy import Redactor, is_local_provider


def _config(temp_dir, **overrides) -> AppConfig:
    """Тестовая конфигурация с заданными overrides."""
    params = {
        "vault_path": temp_dir / "vault",
        "data_dir": temp_dir / "data",
        "db_path": temp_dir / "data" / "state.db",
        "sources_path": temp_dir / "data" / "sources.yaml",
        "profile_path": temp_dir / "data" / "user_profile.yaml",
        "log_path": temp_dir / "data" / "kb_agent.log",
        "llm_provider": "openai",
        "openai_api_key": "sk-test-key",
        "openai_base_url": "https://api.example.com/v1",
        "openai_model": "test-model",
    }
    params.update(overrides)
    return AppConfig(**params)


class TestLocalProcessingMode:
    """Режим локальной обработки действительно блокирует внешний LLM."""

    def test_external_provider_blocked_in_local_mode(self, temp_dir):
        """В локальном режиме запрос к внешнему провайдеру не выполняется."""
        config = _config(temp_dir, local_processing_mode=True)
        client = LLMClient(config)

        post = MagicMock()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("httpx.Client.post", post)
            response = client.complete([{"role": "user", "content": "секретные данные"}])

        assert response.error is not None
        assert "локальной обработки" in response.error.lower()
        post.assert_not_called()

    def test_local_provider_allowed_in_local_mode(self, temp_dir, monkeypatch):
        """Локальный провайдер в локальном режиме работает как обычно."""
        config = _config(
            temp_dir,
            local_processing_mode=True,
            llm_provider="ollama",
            openai_base_url="http://localhost:11434/v1",
            openai_api_key=None,
        )
        client = LLMClient(config)
        assert client.is_local is True
        assert client.privacy_policy_error() is None

    def test_privacy_policy_error_message(self, temp_dir):
        """При нарушении политики возвращается понятное сообщение."""
        config = _config(temp_dir, local_processing_mode=True)
        client = LLMClient(config)
        error = client.privacy_policy_error()
        assert error is not None
        assert "openai" in error

    def test_external_provider_allowed_without_local_mode(self, temp_dir, monkeypatch):
        """Без локального режима внешний провайдер работает."""
        config = _config(temp_dir)
        client = LLMClient(config)
        assert client.privacy_policy_error() is None
        assert client.is_local is False


class TestRedaction:
    """Redaction полей и источников реально применяется к запросам."""

    def test_email_is_redacted_in_request(self, temp_dir, monkeypatch):
        """Email не уходит в запрос к внешнему LLM."""
        config = _config(temp_dir, redact_fields="email")
        client = LLMClient(config)

        captured: dict = {}

        def fake_post(self, url, headers=None, json=None, **kwargs):
            captured["payload"] = json
            response = MagicMock()
            response.status_code = 200
            response.json.return_value = {
                "choices": [{"message": {"content": "ок"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }
            return response

        monkeypatch.setattr("httpx.Client.post", fake_post)
        client.complete([{"role": "user", "content": "Пишите на test@example.com"}])

        content = captured["payload"]["messages"][0]["content"]
        assert "test@example.com" not in content
        assert "REDACTED" in content

    def test_phone_is_redacted(self, temp_dir):
        """Телефон вырезается из текста запроса."""
        config = _config(temp_dir, redact_fields="phone")
        client = LLMClient(config)
        redacted = client.redact("Мой номер +7 (999) 123-45-67, звоните")
        assert "+7 (999) 123-45-67" not in redacted
        assert "REDACTED" in redacted

    def test_url_is_redacted(self, temp_dir):
        """URL источника вырезается из текста запроса."""
        config = _config(temp_dir, redact_fields="canonical_url")
        client = LLMClient(config)
        redacted = client.redact("Источник: https://t.me/s/private_channel")
        assert "https://t.me/s/private_channel" not in redacted
        assert "REDACTED" in redacted

    def test_local_provider_keeps_data_intact(self, temp_dir):
        """Локальный провайдер получает данные без редактирования."""
        config = _config(
            temp_dir,
            llm_provider="ollama",
            openai_base_url="http://localhost:11434/v1",
            openai_api_key=None,
            redact_fields="email",
        )
        client = LLMClient(config)
        text = "Пишите на test@example.com"
        assert client.redact(text) == text

    def test_source_exclusion_for_external_provider(self, temp_dir):
        """Источник из KB_REDACT_SOURCES исключается для внешнего провайдера."""
        config = _config(temp_dir, redact_sources="tg_secret")
        client = LLMClient(config)
        assert client.is_source_excluded("tg_secret") is True
        assert client.is_source_excluded("tg_public") is False

    def test_source_exclusion_disabled_for_local_provider(self, temp_dir):
        """Для локального провайдера источники не исключаются."""
        config = _config(
            temp_dir,
            llm_provider="ollama",
            openai_base_url="http://localhost:11434/v1",
            openai_api_key=None,
            redact_sources="tg_secret",
        )
        client = LLMClient(config)
        assert client.is_source_excluded("tg_secret") is False

    def test_summarize_blocked_for_excluded_source(self, temp_dir):
        """Суммаризация исключённого источника не вызывает LLM."""
        config = _config(temp_dir, redact_sources="tg_secret")
        client = LLMClient(config)
        response = client.summarize("текст", "telegram", "tg_secret")
        assert response.error is not None
        assert "redaction" in response.error

    def test_search_context_excludes_redacted_source(self, temp_dir, state):
        """Заметки исключённого источника не попадают в контекст LLM-ответа."""
        config = _config(temp_dir, redact_sources="tg_secret")
        state.init_db()
        state.index_note(
            note_id="note_secret",
            title="Секретная заметка",
            tags="",
            topic="",
            content="Секретная информация про проект",
            source_id="tg_secret",
        )
        state.index_note(
            note_id="note_open",
            title="Открытая заметка",
            tags="",
            topic="",
            content="Открытая информация про проект",
            source_id="tg_open",
        )

        llm = LLMClient(config)
        captured: dict = {}

        def fake_complete(messages, **kwargs):
            captured["messages"] = messages
            response = MagicMock()
            response.error = None
            response.text = "ответ"
            response.prompt_tokens = 1
            response.completion_tokens = 1
            response.latency_ms = 1.0
            return response

        llm.complete = fake_complete  # type: ignore[method-assign]
        engine = SearchEngine(config, state, llm)
        engine.search("информация про проект", limit=10)

        context = captured["messages"][1]["content"]
        assert "Секретная заметка" not in context
        assert "Открытая заметка" in context


class TestRedactorUnit:
    """Юнит-тесты редактора."""

    def test_unknown_field_falls_back_to_line_removal(self):
        """Неизвестное имя поля вырезает строку целиком."""
        redactor = Redactor(fields=("номер_договора",))
        result = redactor.redact_text("Обычный текст\nномер_договора: 12345\nЕщё текст")
        assert "12345" not in result
        assert "Обычный текст" in result

    def test_empty_redactor_keeps_text(self):
        """Пустой список полей не меняет текст."""
        assert Redactor().redact_text("любой текст") == "любой текст"

    def test_source_matching_is_case_insensitive(self):
        """Идентификатор источника сравнивается без учёта регистра."""
        redactor = Redactor(sources=("tg_secret",))
        assert redactor.is_source_redacted("TG_SECRET") is True
        assert redactor.is_source_redacted("TG_OTHER") is False

    @pytest.mark.parametrize(
        ("provider", "base_url", "expected"),
        [
            ("ollama", "http://localhost:11434/v1", True),
            ("local", "http://127.0.0.1:8080/v1", True),
            ("custom", "http://localhost:9000/v1", True),
            ("custom", "https://api.example.com/v1", False),
            ("openai", "https://api.openai.com/v1", False),
            ("openrouter", "https://openrouter.ai/api/v1", False),
        ],
    )
    def test_is_local_provider(self, provider, base_url, expected):
        """Локальные провайдеры определяются по имени и по loopback-хосту."""
        assert is_local_provider(provider, base_url) is expected
