"""Тесты MCP-сервера (P1-02, P1-08)."""

from __future__ import annotations

from kb_agent.agents.mcp_server import handle_list_tools


class TestMcpCapabilities:
    """P1-08: MCP сервер объявляет capability tools."""

    def test_no_confirm_change_tool(self):
        """P1-02: Модель не имеет инструмента kb_confirm_change."""
        import asyncio

        tools = asyncio.run(handle_list_tools())
        tool_names = [t.name for t in tools]

        assert "kb_confirm_change" not in tool_names, \
            f"kb_confirm_change не должен быть доступен модели. Инструменты: {tool_names}"

        # Write-инструменты должны быть доступны (создают pending, но не применяют)
        assert "kb_add_source" in tool_names
        assert "kb_remove_source" in tool_names
        assert "kb_enable_source" in tool_names
        assert "kb_update_profile" in tool_names
        assert "kb_revert_prompt" in tool_names

        # Read-only инструменты
        assert "kb_search" in tool_names
        assert "kb_get_note" in tool_names
        assert "kb_get_insights" in tool_names