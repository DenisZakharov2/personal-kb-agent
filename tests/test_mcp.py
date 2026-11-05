"""Тесты MCP-сервера (P1-02, P1-08)."""

from __future__ import annotations

from kb_agent.agents.mcp_server import handle_call_tool, handle_list_tools


class TestMcpCapabilities:
    """P1-08: MCP сервер объявляет capability tools."""

    def test_no_confirm_change_tool(self):
        """P1-02: Модель не имеет инструмента kb_confirm_change."""
        import asyncio

        tools = asyncio.run(handle_list_tools())
        tool_names = [t.name for t in tools]

        assert "kb_confirm_change" not in tool_names, \
            f"kb_confirm_change не должен быть доступен модели. Инструменты: {tool_names}"
        assert not any("confirm" in name for name in tool_names)

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

    def test_confirm_change_call_returns_unknown_tool(self, config, monkeypatch):
        """P1-02: вызов kb_confirm_change через tools/call возвращает unknown_tool."""
        import asyncio

        import kb_agent.agents.mcp_server as mcp_server_mod
        from mcp.types import CallToolResult

        monkeypatch.setattr(mcp_server_mod, "load_config", lambda: config)

        result = asyncio.run(handle_call_tool("kb_confirm_change", {"change_id": "any-id"}))

        assert isinstance(result, CallToolResult)
        assert result.isError is True
        assert result.content[0].type == "text"
        assert "Неизвестный инструмент" in result.content[0].text
        assert result.structuredContent is not None
        assert result.structuredContent["code"] == "unknown_tool"