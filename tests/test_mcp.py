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
        assert "kb_sources_status" in tool_names

    def test_confirm_change_call_returns_unknown_tool(self, config, monkeypatch):
        """P1-02: вызов kb_confirm_change через tools/call возвращает unknown_tool."""
        import asyncio

        from mcp.types import CallToolResult

        import kb_agent.agents.mcp_server as mcp_server_mod

        monkeypatch.setattr(mcp_server_mod, "load_config", lambda: config)

        result = asyncio.run(handle_call_tool("kb_confirm_change", {"change_id": "any-id"}))

        assert isinstance(result, CallToolResult)
        assert result.isError is True
        assert result.content[0].type == "text"
        assert "Неизвестный инструмент" in result.content[0].text
        assert result.structuredContent is not None
        assert result.structuredContent["code"] == "unknown_tool"

    def test_kb_sources_status_returns_health(self, config, state, monkeypatch):
        """kb_sources_status возвращает health/статус всех источников."""
        import asyncio

        import kb_agent.agents.mcp_server as mcp_server_mod

        monkeypatch.setattr(mcp_server_mod, "load_config", lambda: config)

        # Добавляем источники в SQLite canonical store
        conn = state.connect()
        conn.execute(
            "INSERT INTO sources (source_id, source_type, channel, enabled) "
            "VALUES ('tg_rubiconzone','telegram','https://t.me/s/rubiconzone',1)"
        )
        conn.execute(
            "INSERT INTO sources (source_id, source_type, channel, enabled) "
            "VALUES ('yt_amorev','youtube','https://youtube.com/@amorev',0)"
        )
        conn.commit()

        text, structured = asyncio.run(handle_call_tool("kb_sources_status", {}))

        assert isinstance(text, list)
        assert structured is not None
        assert structured["total"] == 2
        assert structured["enabled"] == 1
        assert structured["disabled"] == 1
        source_ids = {s["source_id"] for s in structured["sources"]}
        assert source_ids == {"tg_rubiconzone", "yt_amorev"}

        tg = [s for s in structured["sources"] if s["source_id"] == "tg_rubiconzone"][0]
        assert tg["enabled"] is True
        assert tg["source_type"] == "telegram"

        yt = [s for s in structured["sources"] if s["source_id"] == "yt_amorev"][0]
        assert yt["enabled"] is False
        assert yt["source_type"] == "youtube"

        # Текстовый отчёт содержит названия источников и статусы
        text_joined = "\n".join(t.text for t in text if hasattr(t, "text"))
        assert "tg_rubiconzone" in text_joined
        assert "yt_amorev" in text_joined
        assert "включён" in text_joined
        assert "выключен" in text_joined


class TestMcpProposals:
    """MCP-инструменты自我улучшения: список, получение, отклонение предложений."""

    def test_list_proposals_empty(self, config, state, monkeypatch):
        """kb_list_proposals без предложений — пустой список."""
        import asyncio

        import kb_agent.agents.mcp_server as mcp_server_mod

        monkeypatch.setattr(mcp_server_mod, "load_config", lambda: config)

        text, structured = asyncio.run(handle_call_tool("kb_list_proposals", {}))

        assert structured is not None
        assert structured["status"] == "proposed"
        assert structured["count"] == 0
        assert structured["proposals"] == []

    def test_list_proposals_with_data(self, config, state, monkeypatch):
        """kb_list_proposals возвращает созданные предложения."""
        import asyncio

        import kb_agent.agents.mcp_server as mcp_server_mod

        monkeypatch.setattr(mcp_server_mod, "load_config", lambda: config)

        state.save_proposal(
            proposal_id="prop-prompt-1",
            target="prompt",
            target_name="agent_prompt",
            current_version=1,
            proposed_version=2,
            diff="diff здесь",
            reason="улучшить суммаризацию",
        )
        state.save_proposal(
            proposal_id="prop-config-1",
            target="config",
            target_name="data/sources.yaml",
            current_version=None,
            proposed_version=None,
            diff="diff конфиг",
            reason="сменить источник",
        )
        # Переводим одно в rejected для проверки фильтра.
        state.update_proposal("prop-config-1", status="rejected")

        text, structured = asyncio.run(handle_call_tool("kb_list_proposals", {}))

        assert structured is not None
        assert structured["status"] == "proposed"
        assert structured["count"] == 1
        assert structured["proposals"][0]["proposal_id"] == "prop-prompt-1"

        text, structured = asyncio.run(
            handle_call_tool("kb_list_proposals", {"status": "rejected"})
        )
        assert structured["count"] == 1
        assert structured["proposals"][0]["proposal_id"] == "prop-config-1"

    def test_list_proposals_invalid_status(self, config, state, monkeypatch):
        """kb_list_proposals с недопустимым статусом — ошибка."""
        import asyncio

        import kb_agent.agents.mcp_server as mcp_server_mod

        monkeypatch.setattr(mcp_server_mod, "load_config", lambda: config)

        result = asyncio.run(handle_call_tool("kb_list_proposals", {"status": "bogus"}))

        assert result.isError is True
        assert result.structuredContent["code"] == "invalid_argument"

    def test_get_proposal_found(self, config, state, monkeypatch):
        """kb_get_proposal возвращает детали существующего предложения."""
        import asyncio

        import kb_agent.agents.mcp_server as mcp_server_mod

        monkeypatch.setattr(mcp_server_mod, "load_config", lambda: config)

        state.save_proposal(
            proposal_id="prop-get-1",
            target="prompt",
            target_name="agent_prompt",
            current_version=1,
            proposed_version=2,
            diff="--- a\n+++ b\n",
            reason="причина",
        )

        text, structured = asyncio.run(
            handle_call_tool("kb_get_proposal", {"proposal_id": "prop-get-1"})
        )

        assert structured is not None
        assert structured["proposal_id"] == "prop-get-1"
        assert structured["target"] == "prompt"
        assert structured["target_name"] == "agent_prompt"
        assert structured["status"] == "proposed"
        assert structured["reason"] == "причина"

    def test_get_proposal_not_found(self, config, state, monkeypatch):
        """kb_get_proposal для несуществующего ID — ошибка not_found."""
        import asyncio

        import kb_agent.agents.mcp_server as mcp_server_mod

        monkeypatch.setattr(mcp_server_mod, "load_config", lambda: config)

        result = asyncio.run(
            handle_call_tool("kb_get_proposal", {"proposal_id": "нет такого"})
        )

        assert result.isError is True
        assert result.structuredContent["code"] == "not_found"

    def test_reject_proposal_creates_pending(self, config, state, monkeypatch):
        """kb_reject_proposal создаёт pending-запрос (требует подтверждения)."""
        import asyncio

        import kb_agent.agents.mcp_server as mcp_server_mod

        monkeypatch.setattr(mcp_server_mod, "load_config", lambda: config)

        state.save_proposal(
            proposal_id="prop-rej-1",
            target="prompt",
            target_name="agent_prompt",
            current_version=1,
            proposed_version=2,
            diff="diff",
            reason="старая причина",
        )

        result = asyncio.run(
            handle_call_tool(
                "kb_reject_proposal", {"proposal_id": "prop-rej-1", "reason": "не актуально"}
            )
        )

        assert isinstance(result, list)
        text_joined = "\n".join(t.text for t in result if hasattr(t, "text"))
        assert "prop-rej-1" in text_joined
        assert "pending" in text_joined

        # Предложение пока ещё не отклонено (ожидает подтверждения).
        assert state.get_proposal("prop-rej-1")["status"] == "proposed"

    def test_reject_proposal_then_confirm(self, config, state, monkeypatch):
        """После подтверждения pending-запроса предложение становится rejected."""
        import asyncio

        import kb_agent.agents.mcp_server as mcp_server_mod
        from kb_agent.storage.pending import PendingManager

        monkeypatch.setattr(mcp_server_mod, "load_config", lambda: config)

        state.save_proposal(
            proposal_id="prop-rej-confirm-1",
            target="config",
            target_name="data/sources.yaml",
            current_version=None,
            proposed_version=None,
            diff="diff",
            reason="причина",
        )

        result = asyncio.run(
            handle_call_tool(
                "kb_reject_proposal", {"proposal_id": "prop-rej-confirm-1"}
            )
        )
        assert isinstance(result, list)
        change_id = None
        for part in result:
            if hasattr(part, "text") and "change_id:" in part.text:
                for line in part.text.splitlines():
                    if line.strip().startswith("change_id:"):
                        change_id = line.split(":", 1)[1].strip()
        assert change_id is not None, result

        # Получаем ревизию pending-запроса и подтверждаем.
        pm = PendingManager(state)
        change = pm.get_change(change_id)
        assert change is not None
        assert change["status"] == "pending"
        assert change["action"] == "reject_proposal"

        success = pm.confirm_change(change_id, revision=change["revision"], confirmed_by="user")
        assert success is True

        assert state.get_proposal("prop-rej-confirm-1")["status"] == "rejected"

    def test_reject_proposal_not_found(self, config, state, monkeypatch):
        """kb_reject_proposal для несуществующего предложения — pending-запрос применится с ошибкой (failed)."""
        import asyncio

        import kb_agent.agents.mcp_server as mcp_server_mod

        monkeypatch.setattr(mcp_server_mod, "load_config", lambda: config)

        result = asyncio.run(
            handle_call_tool("kb_reject_proposal", {"proposal_id": "нет такого"})
        )

        # Инструмент сам создаёт pending-запрос (не ошибается на входе).
        assert isinstance(result, list)
        text_joined = "\n".join(t.text for t in result if hasattr(t, "text"))
        assert "нет такого" in text_joined
        assert "pending" in text_joined