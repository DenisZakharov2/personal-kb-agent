"""Тесты приватности и redaction (6.2).

Проверяется, что приватные заметки не возвращаются внешнему агенту,
поддерживаются метки чувствительности, работает redaction чувствительных полей.
"""

from __future__ import annotations

import asyncio

from mcp.types import CallToolResult

from kb_agent.agents.mcp_server import handle_call_tool
from kb_agent.config import AppConfig
from kb_agent.core.search import SearchEngine
from kb_agent.processing.llm_client import LLMClient


def _is_error_result(result) -> bool:
    return isinstance(result, CallToolResult)


class TestPrivateNotesNotReturnedToExternalAgent:
    """6.2: приватные заметки не выдаются внешнему агенту через MCP."""

    def test_kb_get_note_private_note_forbidden(self, config, monkeypatch, state):
        """kb_get_note для приватной заметки возвращает ошибку forbidden."""
        import kb_agent.agents.mcp_server as mcp_server_mod

        monkeypatch.setattr(mcp_server_mod, "load_config", lambda: config)

        state.init_db()
        state.index_note(
            note_id="note_private",
            title="Приватная заметка",
            tags="",
            topic="",
            content="Секретные данные, не для внешнего агента",
            source_id="tg_private",
            canonical_url="https://t.me/s/private_chan",
            is_private=1,
        )

        result = asyncio.run(handle_call_tool("kb_get_note", {"note_id": "note_private"}))
        assert _is_error_result(result)
        assert result.isError is True
        text = result.content[0].text.lower()
        assert "приватная" in text or "forbidden" in text

    def test_kb_get_note_public_note_returned(self, config, monkeypatch, state):
        """kb_get_note для публичной заметки возвращает содержимое."""
        import kb_agent.agents.mcp_server as mcp_server_mod

        monkeypatch.setattr(mcp_server_mod, "load_config", lambda: config)

        state.init_db()
        state.index_note(
            note_id="note_public",
            title="Публичная заметка",
            tags="",
            topic="",
            content="Открытые данные",
            source_id="tg_public",
            canonical_url="https://t.me/s/public_chan",
            is_private=0,
        )

        result = asyncio.run(handle_call_tool("kb_get_note", {"note_id": "note_public"}))
        assert not _is_error_result(result)
        text, structured = result
        assert structured["content"] == "Открытые данные"
        assert structured["is_private"] is False

    def test_kb_search_marks_private_notes(self, config, monkeypatch, state):
        """kb_search помечает приватные заметки is_private=True в структурированном ответе."""
        import kb_agent.agents.mcp_server as mcp_server_mod

        monkeypatch.setattr(mcp_server_mod, "load_config", lambda: config)

        state.init_db()
        state.index_note(
            note_id="note_pub",
            title="Публичная",
            tags="",
            topic="",
            content="Открытая информация о проекте",
            source_id="tg_pub",
            canonical_url="https://t.me/s/pub",
            is_private=0,
        )
        state.index_note(
            note_id="note_priv",
            title="Приватная",
            tags="",
            topic="",
            content="Секретная информация",
            source_id="tg_priv",
            canonical_url="https://t.me/s/priv",
            is_private=1,
        )

        result = asyncio.run(handle_call_tool("kb_search", {"query": "информация"}))
        text, structured = result
        item_ids = [it["note_id"] for it in structured["items"]]
        assert "note_pub" in item_ids
        # Приватная заметка помечена is_private=True
        priv_items = [it for it in structured["items"] if it["note_id"] == "note_priv"]
        if priv_items:
            assert priv_items[0]["is_private"] is True

    def test_mcp_response_includes_sensitivity_labels(self, config, monkeypatch, state):
        """MCP-ответ содержит метки чувствительности (is_private, sensitivity, untrusted_content)."""
        import kb_agent.agents.mcp_server as mcp_server_mod

        monkeypatch.setattr(mcp_server_mod, "load_config", lambda: config)

        state.init_db()
        state.index_note(
            note_id="note_labelled",
            title="Меченная",
            tags="",
            topic="",
            content="Контент с метками",
            source_id="tg_labelled",
            canonical_url="https://evil.example.com/x",
            is_private=1,
        )

        result = asyncio.run(handle_call_tool("kb_search", {"query": "метки"}))
        text, structured = result
        for item in structured["items"]:
            assert "is_private" in item
            assert "sensitivity" in item
            assert "untrusted_content" in item
            if item["note_id"] == "note_labelled":
                assert item["is_private"] is True
                assert item["sensitivity"] == "private"
                assert item["untrusted_content"] is True  # evil.example.com не в allowlist


class TestRedaction:
    """6.2: redaction исключает чувствительные поля/источники из отправки внешнему LLM."""

    def test_redact_fields_config(self, temp_dir):
        """Конфигурация redaction полей читается из настроек."""
        cfg = AppConfig(
            vault_path=temp_dir / "vault",
            data_dir=temp_dir / "data",
            db_path=temp_dir / "data" / "state.db",
            sources_path=temp_dir / "data" / "sources.yaml",
            profile_path=temp_dir / "data" / "user_profile.yaml",
            log_path=temp_dir / "data" / "kb_agent.log",
            prompts_dir=temp_dir / "config" / "prompts",
            llm_provider="ollama",
            openai_api_key=None,
            openai_model="test-model",
            redact_fields="email,phone,canonical_url",
            redact_sources="tg_secret",
        )
        assert cfg.redact_fields == "email,phone,canonical_url"
        assert cfg.redact_sources == "tg_secret"

    def test_local_processing_mode_config(self, temp_dir):
        """Режим локальной обработки читается из настроек."""
        cfg = AppConfig(
            vault_path=temp_dir / "vault",
            data_dir=temp_dir / "data",
            db_path=temp_dir / "data" / "state.db",
            sources_path=temp_dir / "data" / "sources.yaml",
            profile_path=temp_dir / "data" / "user_profile.yaml",
            log_path=temp_dir / "data" / "kb_agent.log",
            prompts_dir=temp_dir / "config" / "prompts",
            llm_provider="ollama",
            openai_api_key=None,
            openai_model="test-model",
            local_processing_mode=True,
        )
        assert cfg.local_processing_mode is True

    def test_search_result_has_redaction_relevant_fields(self, config, state):
        """Результаты поиска содержат поля, необходимые для redaction на стороне клиента."""
        state.init_db()
        state.index_note(
            note_id="note_redact",
            title="Для реdaction",
            tags="",
            topic="",
            content="Контент",
            source_id="tg_secret",
            canonical_url="https://t.me/s/secret",
        )
        engine = SearchEngine(config, state, LLMClient(config))
        response = engine.search("контент")
        assert len(response.results) == 1
        r = response.results[0]
        assert hasattr(r, "source_id")
        assert hasattr(r, "canonical_url")
        assert hasattr(r, "is_private")
        assert hasattr(r, "sensitivity")
