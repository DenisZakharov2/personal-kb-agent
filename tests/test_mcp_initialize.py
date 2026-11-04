"""Волна 2: MCP initialize smoke test (initialize → tools/list → tools/call)."""

from __future__ import annotations

import asyncio

from mcp.server.models import InitializationOptions
from mcp.types import ServerCapabilities, ToolsCapability

from kb_agent.agents.mcp_server import handle_call_tool, handle_list_tools


class TestMcpInitialize:
    def test_capabilities_tools_present(self):
        """После initialize capabilities.tools должен быть объявлен."""
        options = InitializationOptions(
            server_name="kb-agent",
            server_version="0.1.0",
            capabilities=ServerCapabilities(tools=ToolsCapability(listChanged=False)),
        )
        assert options.capabilities.tools is not None

    def test_initialize_list_tools_call_structured(self, config, monkeypatch):
        """initialize → tools/list → tools/call возвращают структурированные данные."""
        import kb_agent.agents.mcp_server as mcp_server_mod

        monkeypatch.setattr(mcp_server_mod, "load_config", lambda: config)

        # tools/list
        tools = asyncio.run(handle_list_tools())
        assert isinstance(tools, list) and len(tools) > 0
        tool_names = [t.name for t in tools]
        assert "kb_search" in tool_names
        assert "kb_get_note" in tool_names
        assert "kb_get_insights" in tool_names
        assert "kb_confirm_change" not in tool_names

        # tools/call (пустая БД — LLM не вызывается, сеть не нужна)
        result = asyncio.run(handle_call_tool("kb_search", {"query": "тест", "limit": 3}))
        text_parts, structured = result
        assert isinstance(text_parts, list) and len(text_parts) == 1
        assert text_parts[0].type == "text"
        assert isinstance(text_parts[0].text, str) and text_parts[0].text
        # Strukturuierter Inhalt vorhanden
        assert isinstance(structured, dict) and "items" in structured

        # kb_get_note на несуществующий ID — ошибка unknown tool (CallToolResult)
        result_note = asyncio.run(handle_call_tool("kb_get_note", {"note_id": "нет-такого"}))
        from mcp.types import CallToolResult

        assert isinstance(result_note, CallToolResult)
        assert result_note.isError is True
        assert result_note.content[0].type == "text"
        assert "не найдена" in result_note.content[0].text