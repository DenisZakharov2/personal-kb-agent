"""Волна 2: защита от промпт-инъекций (P2).

Модель не имеет подтверждающего инструмента, поэтому вредоносный текст в
заметке не может вызвать удаление источника или подтверждение изменения.
"""

from __future__ import annotations

import asyncio

import pytest

from kb_agent.agents.mcp_server import handle_call_tool, handle_list_tools


class TestPromptInjection:
    def test_model_has_no_confirm_tool(self, config, monkeypatch):
        """P1-02/P2: у модели нет инструмента kb_confirm_change."""
        import kb_agent.agents.mcp_server as mcp_server_mod

        monkeypatch.setattr(mcp_server_mod, "load_config", lambda: config)

        tools = asyncio.run(handle_list_tools())
        names = [t.name for t in tools]

        assert "kb_confirm_change" not in names, (
            f"kb_confirm_change не должен быть доступен модели. Инструменты: {names}"
        )
        assert not any("confirm" in name for name in names)

        # Write-инструменты создают pending-запрос, но не применяют его.
        assert "kb_add_source" in names
        assert "kb_remove_source" in names
        assert "kb_enable_source" in names
        assert "kb_update_profile" in names
        assert "kb_revert_prompt" in names

    def test_malicious_note_cannot_confirm_change(self, config, monkeypatch):
        """Текст заметки с требованием «подтверди удаление» не приводит к
        применению pending-запроса: инструмента подтверждения у модели нет."""
        import kb_agent.agents.mcp_server as mcp_server_mod

        monkeypatch.setattr(mcp_server_mod, "load_config", lambda: config)

        # Попытка вызвать confirm-инструмент по имени — неизвестный инструмент.
        result = asyncio.run(handle_call_tool("kb_confirm_change", {"change_id": "any-id"}))
        assert result and result[0].type == "text"
        assert "Неизвестный инструмент" in result[0].text

        # Список инструментов не содержит ничего, что могло бы применить изменение.
        tools = asyncio.run(handle_list_tools())
        assert all("confirm" not in t.name and "apply" not in t.name for t in tools)