"""MCP-сервер для доступа внешних агентов к базе знаний (Этап 4).

Read-only часть: поиск и чтение заметок, инсайты.
Write-часть (требует подтверждения человека): управление источниками,
обновление профиля, откат промптов.
Поддерживает стандарт протокола Model Context Protocol (MCP) через stdio.

P2-12: инструменты описываются полными JSON-схемами (inputSchema/outputSchema,
additionalProperties: false, перечисления, аннотации), читающие инструменты
возвращают структурированный контент (structuredContent) с пагинацией,
ошибки единой модели.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolResult,
    TextContent,
    Tool,
    ToolAnnotations,
    ServerCapabilities,
    ToolsCapability,
)

from pydantic import BaseModel, Field, ValidationError

from kb_agent.config import load_config
from kb_agent.core.search import SearchEngine
from kb_agent.core.insights import InsightsEngine
from kb_agent.processing.llm_client import LLMClient
from kb_agent.sources.registry import normalize_source
from kb_agent.storage.pending import PendingManager
from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.mcp_server")

app = Server("kb-agent")

# ---------------------------------------------------------------------------
# Allowlist источников и URL (6.1).
#
# Только эти хосты разрешаются для добавления через MCP. yt-dlp поддерживает
# множество протоколов и сайтов, поэтому явно ограничиваем circle доверия
# conocidosими Telegram/YouTube доменами.
# ---------------------------------------------------------------------------

ALLOWED_HOSTS = {
    "t.me",
    "www.t.me",
    "telegram.me",
    "www.telegram.me",
    "youtube.com",
    "www.youtube.com",
    "youtu.be",
    "www.youtu.be",
}

ALLOWED_SOURCE_TYPES = {"telegram", "youtube"}


def _is_url_allowed(url: str) -> bool:
    """Проверяет, что хост URL входит в allowlist (6.1)."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    return host in ALLOWED_HOSTS or host.endswith((".t.me", ".youtube.com", ".youtu.be"))


# ---------------------------------------------------------------------------
# Pydantic-модели для структурированного ввода/вывода (P2-12)
# ---------------------------------------------------------------------------


class SearchItem(BaseModel):
    """Одна найденная заметка в структурированном ответе поиска."""

    note_id: str = Field(description="Идентификатор заметки")
    title: str = Field(description="Заголовок")
    snippet: str = Field(default="", description="Релевантный фрагмент вокруг совпадения")
    score: float = Field(default=0.0, description="Релевантность (0.0 - 1.0, выше — лучше)")
    source_id: Optional[str] = Field(default=None, description="Идентификатор источника")
    external_id: Optional[str] = Field(default=None, description="Внешний идентификатор")
    canonical_url: Optional[str] = Field(default=None, description="URL источника")
    file_path: Optional[str] = Field(default=None, description="Путь к файлу в vault")
    topic: str = Field(default="", description="Тема заметки")
    tags: list[str] = Field(default_factory=list, description="Теги")
    revision: Optional[int] = Field(default=None, description="Ревизия заметки")
    content_hash: Optional[str] = Field(default=None, description="Хэш содержимого")
    # Метаданные происхождения (6.1)
    untrusted_content: bool = Field(
        default=False,
        description="Признак недоверенного контента (источник не из allowlist или метка untrusted)",
    )
    # Метка чувствительности (6.2)
    is_private: bool = Field(
        default=False,
        description="Признак приватной заметки — не выдаётся внешнему агенту без проверки",
    )
    sensitivity: str = Field(
        default="public",
        description="Метка чувствительности: public, internal, private, secret",
    )


class SearchOutput(BaseModel):
    """Структурированный ответ kb_search."""

    items: list[SearchItem] = Field(default_factory=list, description="Найденные заметки")
    next_cursor: Optional[int] = Field(default=None, description="Курсор для следующей страницы")
    total: int = Field(default=0, description="Количество найденных")


class NoteOutput(BaseModel):
    """Структурированный ответ kb_get_note."""

    note_id: str
    title: str
    content: str
    tags: str = ""
    topic: str = ""
    source_id: Optional[str] = None
    external_id: Optional[str] = None
    canonical_url: Optional[str] = None
    file_path: Optional[str] = None
    content_hash: Optional[str] = None
    revision: Optional[int] = None
    # Метаданные происхождения (6.1)
    untrusted_content: bool = Field(
        default=False,
        description="Признак недоверенного контента",
    )
    # Метка чувствительности (6.2)
    is_private: bool = Field(
        default=False,
        description="Признак приватной заметки",
    )
    sensitivity: str = Field(
        default="public",
        description="Метка чувствительности: public, internal, private, secret",
    )


class InsightOutput(BaseModel):
    statement: str
    link_type: str
    confidence: float
    evidence_note_ids: list[str]
    benefit: str


class ErrorOutput(BaseModel):
    """Единая модель ошибки MCP."""

    code: str = Field(description="Код ошибки")
    message: str = Field(description="Описание ошибки")


# ---------------------------------------------------------------------------
# JSON-схемы инструментов (P2-12)
# ---------------------------------------------------------------------------

_SEARCH_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "description": "Поисковый запрос"},
        "limit": {
            "type": "integer",
            "description": "Максимум результатов на страницу",
            "default": 5,
            "minimum": 1,
            "maximum": 100,
        },
        "cursor": {
            "type": "integer",
            "description": "Смещение для пагинации (0 — первая страница)",
            "default": 0,
            "minimum": 0,
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}

_SEARCH_OUTPUT_SCHEMA = SearchOutput.model_json_schema()

_NOTE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "note_id": {"type": "string", "description": "Идентификатор заметки"},
    },
    "required": ["note_id"],
    "additionalProperties": False,
}

_SOURCE_TYPE_ENUM = {"type": "string", "enum": ["telegram", "youtube"]}


_READ_ONLY_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True
)
_WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False
)


def _read_only_tool(name: str, description: str, input_schema: dict, output_schema: Optional[dict] = None) -> Tool:
    return Tool(
        name=name,
        description=description,
        inputSchema=input_schema,
        outputSchema=output_schema,
        annotations=_READ_ONLY_ANNOTATIONS,
    )


def _write_tool(name: str, description: str, input_schema: dict) -> Tool:
    return Tool(
        name=name,
        description=description,
        inputSchema=input_schema,
        annotations=_WRITE_ANNOTATIONS,
    )


# ---------------------------------------------------------------------------
# Инструменты
# ---------------------------------------------------------------------------

_LIST_TOOLS: list[Tool] = [
    _read_only_tool(
        "kb_search",
        "Поиск по заметкам базы знаний через FTS5 с релевантностью и пагинацией.",
        _SEARCH_INPUT_SCHEMA,
        _SEARCH_OUTPUT_SCHEMA,
    ),
    _read_only_tool(
        "kb_get_note",
        "Получение содержимого заметки по её ID.",
        _NOTE_INPUT_SCHEMA,
        NoteOutput.model_json_schema(),
    ),
    _read_only_tool(
        "kb_get_insights",
        "Получение инсайтов базы знаний (связи, повторяющиеся темы).",
        {"type": "object", "properties": {}, "additionalProperties": False},
        InsightOutput.model_json_schema(),
    ),
    _write_tool(
        "kb_add_source",
        "Создать pending-запрос на добавление источника (Telegram/YouTube). Требует подтверждения через отдельный человеческий канал: `kb-agent confirm <change_id>`.",
        {
            "type": "object",
            "properties": {
                "source_type": _SOURCE_TYPE_ENUM,
                "channel": {"type": "string", "description": "Идентификатор канала (например, rubiconzone или @amorev)"},
                "enabled": {"type": "boolean", "description": "Активен ли источник сразу", "default": True},
            },
            "required": ["source_type", "channel"],
            "additionalProperties": False,
        },
    ),
    _write_tool(
        "kb_remove_source",
        "Создать pending-запрос на удаление источника. Требует подтверждения через отдельный человеческий канал: `kb-agent confirm <change_id>`.",
        {
            "type": "object",
            "properties": {
                "source_id": {"type": "string", "description": "Идентификатор источника (например, tg_rubiconzone)"},
            },
            "required": ["source_id"],
            "additionalProperties": False,
        },
    ),
    _write_tool(
        "kb_enable_source",
        "Создать pending-запрос на включение/выключение источника. Требует подтверждения через отдельный человеческий канал: `kb-agent confirm <change_id>`.",
        {
            "type": "object",
            "properties": {
                "source_id": {"type": "string", "description": "Идентификатор источника"},
                "enabled": {"type": "boolean", "description": "Включить или выключить источник"},
            },
            "required": ["source_id", "enabled"],
            "additionalProperties": False,
        },
    ),
    _write_tool(
        "kb_update_profile",
        "Создать pending-запрос на обновление пользовательского профиля. Требует подтверждения через отдельный человеческий канал: `kb-agent confirm <change_id>`.",
        {
            "type": "object",
            "properties": {
                "updates": {
                    "type": "object",
                    "description": "Поля профиля для обновления (цель, задачи, темы и т.д.)",
                },
            },
            "required": ["updates"],
            "additionalProperties": False,
        },
    ),
    _write_tool(
        "kb_revert_prompt",
        "Создать pending-запрос на откат системного промпта к предыдущей версии. Требует подтверждения через отдельный человеческий канал: `kb-agent confirm <change_id>`.",
        {
            "type": "object",
            "properties": {
                "prompt_name": {"type": "string", "description": "Имя промпта (например, agent_prompt)"},
                "version": {"type": "integer", "description": "Версия для отката (по умолчанию — предыдущая перед текущей)"},
            },
            "required": ["prompt_name"],
            "additionalProperties": False,
        },
    ),
]


@app.list_tools()
async def handle_list_tools() -> list[Tool]:
    """Список доступных инструментов."""
    return _LIST_TOOLS


# ---------------------------------------------------------------------------
# Вспомогательные функции ошибок
# ---------------------------------------------------------------------------


def _error_result(code: str, message: str) -> CallToolResult:
    """Единая модель ошибки: structuredContent + isError=True (P2-12)."""
    return CallToolResult(
        content=[TextContent(type="text", text=message)],
        structuredContent=ErrorOutput(code=code, message=message).model_dump(),
        isError=True,
    )


def _ok_text(text: str) -> list[TextContent]:
    return [TextContent(type="text", text=text)]


# ---------------------------------------------------------------------------
# Вызов инструментов
# ---------------------------------------------------------------------------


@app.call_tool()
async def handle_call_tool(name: str, arguments: Optional[dict[str, Any]]) -> Any:
    """Вызов инструмента.

    Возвращает либо CallToolResult (ошибки), либо словарь со структурированным
    контентом (сервер MCP обернёт его в CallToolResult со structuredContent),
    либо список TextContent.
    """
    if arguments is None:
        arguments = {}

    config = load_config()
    state = StateManager(config.db_path)
    llm = LLMClient(config)

    try:
        state.init_db()

        # ---- Читающие инструменты ----

        if name == "kb_search":
            query = arguments.get("query", "") or ""
            limit = int(arguments.get("limit", 5) or 5)
            limit = max(1, min(limit, 100))
            cursor = int(arguments.get("cursor", 0) or 0)
            cursor = max(0, cursor)

            engine = SearchEngine(config, state, llm)
            response = engine.search(query, limit=limit)

            items: list[SearchItem] = []
            for r in response.results[cursor : cursor + limit]:
                prov_parts = []
                if r.source_id:
                    prov_parts.append(f"источник={r.source_id}")
                if r.external_id:
                    prov_parts.append(f"external_id={r.external_id}")
                if r.canonical_url:
                    prov_parts.append(f"url={r.canonical_url}")
                if r.file_path:
                    prov_parts.append(f"путь={r.file_path}")
                if r.revision is not None:
                    prov_parts.append(f"rev={r.revision}")
                if r.content_hash:
                    prov_parts.append(f"hash={r.content_hash[:12]}")
                prov_text = f" [{', '.join(prov_parts)}]" if prov_parts else ""
                items.append(
                    SearchItem(
                        note_id=r.note_id,
                        title=r.title or "Без названия",
                        snippet=r.snippet or "",
                        score=r.score,
                        source_id=r.source_id,
                        external_id=r.external_id,
                        canonical_url=r.canonical_url,
                        file_path=r.file_path,
                        topic=r.topic or "",
                        tags=[t.strip() for t in (r.tags or "").split(",") if t.strip()],
                        revision=r.revision,
                        # Метаданные происхождения (6.1)
                        untrusted_content=not _is_url_allowed(
                            r.canonical_url or ""
                        )
                        if r.canonical_url
                        else True,
                        # Метка чувствительности (6.2)
                        is_private=bool(r.is_private),
                        sensitivity=r.sensitivity,
                    )
                )

            next_cursor = cursor + len(items) if cursor + len(items) < len(response.results) else None
            search_output = SearchOutput(items=items, next_cursor=next_cursor, total=len(response.results))

            lines = [f"## Результаты поиска: {query}", f"Найдено: {len(response.results)}", ""]
            for it in items:
                prov_parts = []
                if it.source_id:
                    prov_parts.append(f"источник={it.source_id}")
                if it.external_id:
                    prov_parts.append(f"external_id={it.external_id}")
                if it.canonical_url:
                    prov_parts.append(f"url={it.canonical_url}")
                if it.file_path:
                    prov_parts.append(f"путь={it.file_path}")
                if it.revision is not None:
                    prov_parts.append(f"rev={it.revision}")
                if it.content_hash:
                    prov_parts.append(f"hash={it.content_hash[:12]}")
                prov_text = f" [{', '.join(prov_parts)}]" if prov_parts else ""
                lines.append(f"- **{it.title}** (ID: {it.note_id}, теги: {', '.join(it.tags)}, тема: {it.topic}, score: {it.score:.2f}{prov_text})")
                snippet = " ".join((it.snippet or "").split())[:300]
                if snippet:
                    lines.append(f"  > {snippet}")
            result_text = "\n".join(lines) if lines else "В базе недостаточно данных по вашему запросу."

            # Возвращаем и текст, и структурированный контент (кортеж).
            return [TextContent(type="text", text=result_text)], search_output.model_dump()

        elif name == "kb_get_note":
            note_id = arguments.get("note_id", "")
            if not note_id:
                return _error_result("invalid_argument", "Поле 'note_id' обязательно")
            note = state.get_note(note_id)
            if note is None:
                return _error_result("not_found", f"Заметка с ID {note_id} не найдена")
            # 6.2: приватные заметки не выдаются внешнему агенту через kb_get_note
            if bool(note.get("is_private", 0)):
                return _error_result(
                    "forbidden",
                    f"Заметка {note_id} помечена как приватная и недоступна "
                    "внешнему агенту через MCP.",
                )
            note_output = NoteOutput(
                note_id=note["note_id"],
                title=note["title"] or "Без названия",
                content=note.get("content", "") or "",
                tags=note["tags"] or "",
                topic=note["topic"] or "",
                source_id=note.get("source_id"),
                external_id=note.get("external_id"),
                canonical_url=note.get("canonical_url"),
                file_path=note.get("file_path"),
                content_hash=note.get("content_hash"),
                revision=note.get("revision"),
                # Метаданные происхождения (6.1)
                untrusted_content=not _is_url_allowed(
                    note.get("canonical_url") or ""
                )
                if note.get("canonical_url")
                else True,
                # Метка чувствительности (6.2)
                is_private=bool(note.get("is_private", 0)),
                sensitivity="private" if note.get("is_private") else "public",
            )
            lines = [
                f"## Заметка: {note_output.title}",
                f"ID: {note_output.note_id}",
                f"Теги: {note_output.tags or '—'}",
                f"Тема: {note_output.topic or '—'}",
                "",
                note_output.content or "Содержимое недоступно.",
            ]
            return [TextContent(type="text", text="\n".join(lines)), note_output.model_dump()]

        elif name == "kb_get_insights":
            insights_engine = InsightsEngine(config, state)
            insights_response = insights_engine.get_insights()
            insights = [
                InsightOutput(
                    statement=i.statement,
                    link_type=i.link_type,
                    confidence=i.confidence,
                    evidence_note_ids=i.evidence_note_ids,
                    benefit=i.benefit,
                )
                for i in insights_response.insights
            ]
            insights_output = {"insights": [i.model_dump() for i in insights]}
            lines = ["## Инсайты базы знаний", ""]
            for i, insight in enumerate(insights, 1):
                lines.append(f"{i}. {insight.statement}")
                lines.append(f"   Тип: {insight.link_type}, уверенность: {insight.confidence}")
                lines.append(f"   Доказательства: {', '.join(insight.evidence_note_ids)}")
                lines.append(f"   Польза: {insight.benefit}")
                lines.append("")
            result_text = "\n".join(lines) if lines else "Инсайтов пока нет."
            return [TextContent(type="text", text=result_text)], insights_output

        # ---- Write-инструменты (Этап 4) ----

        elif name == "kb_add_source":
            source_type = arguments.get("source_type", "")
            channel = arguments.get("channel", "")
            enabled = bool(arguments.get("enabled", True))
            try:
                source_id, channel, source_type = normalize_source(source_type, channel)
            except ValueError as exc:
                return _error_result("invalid_argument", str(exc))
            # 6.1: allowlist хостов — только доверенные домены
            if not _is_url_allowed(channel):
                return _error_result(
                    "invalid_argument",
                    f"URL не в allowlist источников: {channel}. "
                    f"Разрешённые хосты: {', '.join(sorted(ALLOWED_HOSTS))}",
                )

            pm = PendingManager(state)
            change_id = pm.create_change(
                action="add_source",
                entity_type="source",
                entity_id=source_id,
                parameters={"source_id": source_id, "source_type": source_type, "channel": channel, "enabled": enabled},
            )
            lines = [
                "## Pending-запрос на добавление источника",
                f"change_id: {change_id}",
                f"Источник: {source_type} — {channel}",
                f"source_id: {source_id}",
                f"Активен: {enabled}",
                "",
                "Статус: pending (ожидает подтверждения)",
                "Чтобы применить, выполните команду человека: `kb-agent confirm <change_id>`.",
            ]
            return _ok_text("\n".join(lines))

        elif name == "kb_remove_source":
            source_id = arguments.get("source_id", "")
            if not source_id:
                return _error_result("invalid_argument", "Поле 'source_id' обязательно")
            pm = PendingManager(state)
            change_id = pm.create_change(
                action="remove_source", entity_type="source", entity_id=source_id,
                parameters={"source_id": source_id},
            )
            lines = [
                "## Pending-запрос на удаление источника",
                f"change_id: {change_id}",
                f"source_id: {source_id}",
                "",
                "Статус: pending (ожидает подтверждения)",
                "Чтобы применить, выполните команду человека: `kb-agent confirm <change_id>`.",
            ]
            return _ok_text("\n".join(lines))

        elif name == "kb_enable_source":
            source_id = arguments.get("source_id", "")
            enabled = bool(arguments.get("enabled", True))
            if not source_id:
                return _error_result("invalid_argument", "Поле 'source_id' обязательно")
            pm = PendingManager(state)
            change_id = pm.create_change(
                action="enable_source",
                entity_type="source",
                entity_id=source_id,
                parameters={"source_id": source_id, "enabled": enabled},
            )
            lines = [
                f"## Pending-запрос на {'включение' if enabled else 'выключение'} источника",
                f"change_id: {change_id}",
                f"source_id: {source_id}",
                f"enabled: {enabled}",
                "",
                "Статус: pending (ожидает подтверждения)",
                "Чтобы применить, выполните команду человека: `kb-agent confirm <change_id>`.",
            ]
            return _ok_text("\n".join(lines))

        elif name == "kb_update_profile":
            updates = arguments.get("updates", {})
            if not isinstance(updates, dict):
                return _error_result("invalid_argument", "Поле 'updates' должно быть объектом.")
            pm = PendingManager(state)
            change_id = pm.create_change(
                action="update_profile", entity_type="profile", entity_id="user",
                parameters={"updates": updates},
            )
            lines = [
                "## Pending-запрос на обновление профиля",
                f"change_id: {change_id}",
                f"Поля для обновления: {', '.join(updates.keys())}",
                "",
                "Статус: pending (ожидает подтверждения)",
                "Чтобы применить, выполните команду человека: `kb-agent confirm <change_id>`.",
            ]
            return _ok_text("\n".join(lines))

        elif name == "kb_revert_prompt":
            prompt_name = arguments.get("prompt_name", "")
            version = arguments.get("version", None)
            if not prompt_name:
                return _error_result("invalid_argument", "Поле 'prompt_name' обязательно")
            pm = PendingManager(state)
            change_id = pm.create_change(
                action="revert_prompt", entity_type="prompt", entity_id=prompt_name,
                parameters={"prompt_name": prompt_name, "version": version},
            )
            lines = [
                "## Pending-запрос на откат промпта",
                f"change_id: {change_id}",
                f"prompt_name: {prompt_name}",
                f"version: {version if version is not None else 'предыдущая'}",
                "",
                "Статус: pending (ожидает подтверждения)",
                "Чтобы применить, выполните команду человека: `kb-agent confirm <change_id>`.",
            ]
            return _ok_text("\n".join(lines))

        else:
            return _error_result("unknown_tool", f"Неизвестный инструмент: {name}")

    except ValidationError as exc:
        return _error_result("validation_error", str(exc))
    except Exception:  # noqa: BLE001
        logger.exception("Ошибка вызова MCP-инструмента %s", name)
        return _error_result("internal_error", f"Внутренняя ошибка инструмента {name}")
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
