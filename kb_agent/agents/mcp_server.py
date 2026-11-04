"""MCP-сервер для доступа внешних агентов к базе знаний (Этап 4).

Read-only часть: поиск и чтение заметок, инсайты.
Write-часть (требует подтверждения человеком): управление источниками,
обновление профиля, откат промптов.
Поддерживает стандарт протокола Model Context Protocol (MCP) через stdio.
"""

from __future__ import annotations

import asyncio
from typing import Any

from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import (
    TextContent,
    Tool,
    ServerCapabilities,
    ToolsCapability,
)

from kb_agent.config import load_config
from kb_agent.core.search import SearchEngine
from kb_agent.core.insights import InsightsEngine
from kb_agent.processing.llm_client import LLMClient
from kb_agent.storage.pending import PendingManager
from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.mcp_server")

app = Server("kb-agent")


@app.list_tools()
async def handle_list_tools() -> list[Tool]:
    """Список доступных инструментов (read-only)."""
    return [
        Tool(
            name="kb_search",
            description="Поиск по заметкам базы знаний через FTS5",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Поисковый запрос",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Максимум результатов",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 20,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="kb_get_note",
            description="Получение содержимого заметки по её ID",
            inputSchema={
                "type": "object",
                "properties": {
                    "note_id": {
                        "type": "string",
                        "description": "Идентификатор заметки",
                    },
                },
                "required": ["note_id"],
            },
        ),
        Tool(
            name="kb_get_insights",
            description="Получение инсайтов базы знаний (связи, повторяющиеся темы)",
            inputSchema={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        ),
        Tool(
            name="kb_add_source",
            description="Создать pending-запрос на добавление источника (Telegram/YouTube). Требует подтверждения через kb_confirm_change.",
            inputSchema={
                "type": "object",
                "properties": {
                    "source_type": {
                        "type": "string",
                        "description": "Тип источника: telegram или youtube",
                    },
                    "channel": {
                        "type": "string",
                        "description": "Идентификатор канала (например, rubiconzone или @amorev)",
                    },
                    "enabled": {
                        "type": "boolean",
                        "description": "Активен ли источник сразу",
                        "default": True,
                    },
                },
                "required": ["source_type", "channel"],
            },
        ),
        Tool(
            name="kb_remove_source",
            description="Создать pending-запрос на удаление источника. Требует подтверждения через kb_confirm_change.",
            inputSchema={
                "type": "object",
                "properties": {
                    "source_id": {
                        "type": "string",
                        "description": "Идентификатор источника (например, tg_rubiconzone)",
                    },
                },
                "required": ["source_id"],
            },
        ),
        Tool(
            name="kb_enable_source",
            description="Создать pending-запрос на включение/выключение источника. Требует подтверждения через kb_confirm_change.",
            inputSchema={
                "type": "object",
                "properties": {
                    "source_id": {
                        "type": "string",
                        "description": "Идентификатор источника",
                    },
                    "enabled": {
                        "type": "boolean",
                        "description": "Включить или выключить источник",
                    },
                },
                "required": ["source_id", "enabled"],
            },
        ),
        Tool(
            name="kb_update_profile",
            description="Создать pending-запрос на обновление пользовательского профиля. Требует подтверждения через kb_confirm_change.",
            inputSchema={
                "type": "object",
                "properties": {
                    "updates": {
                        "type": "object",
                        "description": "Поля профиля для обновления (цель, задачи, темы и т.д.)",
                    },
                },
                "required": ["updates"],
            },
        ),
        Tool(
            name="kb_revert_prompt",
            description="Создать pending-запрос на откат системного промпта к предыдущей версии. Требует подтверждения через kb_confirm_change.",
            inputSchema={
                "type": "object",
                "properties": {
                    "prompt_name": {
                        "type": "string",
                        "description": "Имя промпта (например, agent_prompt)",
                    },
                    "version": {
                        "type": "integer",
                        "description": "Версия для отката (по умолчанию — предыдущая перед текущей)",
                    },
                },
                "required": ["prompt_name"],
            },
        ),
    ]


@app.call_tool()
async def handle_call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    """Вызов инструмента."""
    config = load_config()
    state = StateManager(config.db_path)
    llm = LLMClient(config)

    try:
        state.init_db()

        if name == "kb_search":
            query = arguments.get("query", "")
            limit = arguments.get("limit", 5)
            engine = SearchEngine(config, state, llm)
            response = engine.search(query, limit=limit)

            if not response.results:
                result_text = "В базе недостаточно данных по вашему запросу."
            else:
                lines = [f"## Результаты поиска: {query}", f"Найдено: {len(response.results)}", ""]
                for r in response.results:
                    prov = []
                    if r.source_id:
                        prov.append(f"источник={r.source_id}")
                    if r.external_id:
                        prov.append(f"external_id={r.external_id}")
                    if r.canonical_url:
                        prov.append(f"url={r.canonical_url}")
                    if r.file_path:
                        prov.append(f"путь={r.file_path}")
                    if r.revision is not None:
                        prov.append(f"rev={r.revision}")
                    if r.content_hash:
                        prov.append(f"hash={r.content_hash[:12]}")
                    prov_text = f" [{', '.join(prov)}]" if prov else ""
                    lines.append(
                        f"- **{r.title}** (ID: {r.note_id}, теги: {r.tags}, тема: {r.topic}{prov_text})"
                    )
                    snippet = " ".join((r.content or "").split())[:300]
                    if snippet:
                        lines.append(f"  > {snippet}")
                result_text = "\n".join(lines)

            return [TextContent(type="text", text=result_text)]

        elif name == "kb_get_note":
            note_id = arguments.get("note_id", "")
            note = state.get_note(note_id)

            if note is None:
                return [TextContent(type="text", text=f"Заметка с ID {note_id} не найдена.")]

            lines = [
                f"## Заметка: {note['title'] or 'Без названия'}",
                f"ID: {note['note_id']}",
                f"Теги: {note['tags'] or '—'}",
                f"Тема: {note['topic'] or '—'}",
                "",
                note.get("content", "") or "Содержимое недоступно.",
            ]
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "kb_get_insights":
            engine = InsightsEngine(config, state)
            response = engine.get_insights()
            if not response.insights:
                return [TextContent(type="text", text="Инсайтов пока нет.")]
            lines = ["## Инсайты базы знаний", ""]
            for i, insight in enumerate(response.insights, 1):
                lines.append(f"{i}. {insight.statement}")
                lines.append(f"   Тип: {insight.link_type}, уверенность: {insight.confidence}")
                lines.append(f"   Доказательства: {', '.join(insight.evidence_note_ids)}")
                lines.append(f"   Польза: {insight.benefit}")
                lines.append("")
            return [TextContent(type="text", text="\n".join(lines))]

        # ---- Write-инструменты (Этап 4) ----
        # Все создают pending-запрос, за исключением kb_confirm_change, который применяет.

        elif name == "kb_add_source":
            source_type = arguments.get("source_type", "")
            channel = arguments.get("channel", "")
            enabled = arguments.get("enabled", True)
            if source_type not in ("telegram", "youtube"):
                return [TextContent(type="text", text=f"Неподдерживаемый тип источника: {source_type}. Доступны: telegram, youtube.")]

            pm = PendingManager(state)
            prefix = "tg" if source_type == "telegram" else "yt"
            channel_clean = channel.replace("@", "").replace("/", "_")
            source_id = f"{prefix}_{channel_clean}"
            change_id = pm.create_change(
                action="add_source",
                entity_type="source",
                entity_id=source_id,
                parameters={
                    "source_id": source_id,
                    "source_type": source_type,
                    "channel": channel,
                    "enabled": bool(enabled),
                },
            )
            lines = [
                "## Pending-запрос на добавление источника",
                f"change_id: {change_id}",
                f"Источник: {source_type} — {channel}",
                f"source_id: {source_id}",
                f"Активен: {enabled}",
                "",
                "Статус: pending (ожидает подтверждения)",
                "Чтобы применить, вызовите kb_confirm_change с этим change_id.",
            ]
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "kb_remove_source":
            source_id = arguments.get("source_id", "")
            pm = PendingManager(state)
            change_id = pm.create_change(
                action="remove_source",
                entity_type="source",
                entity_id=source_id,
                parameters={"source_id": source_id},
            )
            lines = [
                "## Pending-запрос на удаление источника",
                f"change_id: {change_id}",
                f"source_id: {source_id}",
                "",
                "Статус: pending (ожидает подтверждения)",
                "Чтобы применить, вызовите kb_confirm_change с этим change_id.",
            ]
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "kb_enable_source":
            source_id = arguments.get("source_id", "")
            enabled = arguments.get("enabled", True)
            pm = PendingManager(state)
            change_id = pm.create_change(
                action="enable_source",
                entity_type="source",
                entity_id=source_id,
                parameters={"source_id": source_id, "enabled": bool(enabled)},
            )
            lines = [
                f"## Pending-запрос на {'включение' if enabled else 'выключение'} источника",
                f"change_id: {change_id}",
                f"source_id: {source_id}",
                f"enabled: {enabled}",
                "",
                "Статус: pending (ожидает подтверждения)",
                "Чтобы применить, вызовите kb_confirm_change с этим change_id.",
            ]
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "kb_update_profile":
            updates = arguments.get("updates", {})
            if not isinstance(updates, dict):
                return [TextContent(type="text", text="Поле 'updates' должно быть объектом.")]
            pm = PendingManager(state)
            change_id = pm.create_change(
                action="update_profile",
                entity_type="profile",
                entity_id="user",
                parameters={"updates": updates},
            )
            lines = [
                "## Pending-запрос на обновление профиля",
                f"change_id: {change_id}",
                f"Поля для обновления: {', '.join(updates.keys())}",
                "",
                "Статус: pending (ожидает подтверждения)",
                "Чтобы применить, вызовите kb_confirm_change с этим change_id.",
            ]
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "kb_revert_prompt":
            prompt_name = arguments.get("prompt_name", "")
            version = arguments.get("version", None)
            pm = PendingManager(state)
            change_id = pm.create_change(
                action="revert_prompt",
                entity_type="prompt",
                entity_id=prompt_name,
                parameters={"prompt_name": prompt_name, "version": version},
            )
            lines = [
                "## Pending-запрос на откат промпта",
                f"change_id: {change_id}",
                f"prompt_name: {prompt_name}",
                f"version: {version if version is not None else 'предыдущая'}",
                "",
                "Статус: pending (ожидает подтверждения)",
                "Чтобы применить, вызовите kb_confirm_change с этим change_id.",
            ]
            return [TextContent(type="text", text="\n".join(lines))]

        else:
            return [TextContent(type="text", text=f"Неизвестный инструмент: {name}")]

    finally:
        state.close()


async def main() -> None:
    """Точка входа для MCP-сервера через stdio."""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="kb-agent",
                server_version="0.1.0",
                capabilities=ServerCapabilities(
                    tools=ToolsCapability(listChanged=False),
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
