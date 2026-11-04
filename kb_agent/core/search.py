"""Поисковая система базы знаний (Этап 3).

Поиск через SQLite FTS5 + LLM-синтез ответа со ссылками на заметки.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from kb_agent.config import AppConfig
from kb_agent.core.prompts import get_active_system_prompt
from kb_agent.processing.llm_client import LLMClient
from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.core.search")


@dataclass
class SearchResult:
    """Результат поиска одной заметки."""

    note_id: str
    title: str
    tags: str
    topic: str
    content: str
    score: float = 0.0
    # Поля provenience (из таблицы notes): источник, путь, версия и hash.
    canonical_url: Optional[str] = None
    source_id: Optional[str] = None
    external_id: Optional[str] = None
    file_path: Optional[str] = None
    content_hash: Optional[str] = None
    revision: Optional[int] = None


@dataclass
class SearchResponse:
    """Ответ на поисковый запрос."""

    query: str
    results: list[SearchResult] = field(default_factory=list)
    answer: Optional[str] = None
    sources: list[str] = field(default_factory=list)
    enough_data: bool = True


class SearchEngine:
    """Поисковая система: FTS5 -> отбор топ-N -> LLM-ответ со ссылками."""

    def __init__(self, config: AppConfig, state: StateManager, llm: LLMClient) -> None:
        self.config = config
        self.state = state
        self.llm = llm

    def search(self, query: str, limit: int = 5) -> SearchResponse:
        """Ищет заметки по запросу и возвращает релевантные результаты."""
        logger.info("Поиск: %s", query)

        # FTS5 поиск
        rows = self.state.search(query, limit=limit)
        results: list[SearchResult] = []
        for row in rows:
            results.append(
                SearchResult(
                    note_id=row["note_id"],
                    title=row["title"] or "Без названия",
                    tags=row["tags"] or "",
                    topic=row["topic"] or "",
                    content=row.get("content", "") or "",
                    canonical_url=row.get("canonical_url"),
                    source_id=row.get("source_id"),
                    external_id=row.get("external_id"),
                    file_path=row.get("file_path"),
                    content_hash=row.get("content_hash"),
                    revision=row.get("revision"),
                )
            )

        response = SearchResponse(query=query, results=results)

        if not results:
            response.enough_data = False
            response.answer = "В базе недостаточно данных по вашему запросу."
            return response

        # Формируем контекст для LLM из топ-N заметок
        context_parts: list[str] = []
        sources: list[str] = []
        for r in results:
            context_parts.append(
                f"--- Заметка: {r.title} (ID: {r.note_id}, теги: {r.tags}) ---\n{r.content[:1500]}"
            )
            sources.append(f"{r.title} (ID: {r.note_id})")

        context = "\n\n".join(context_parts)

        # LLM-ответ на основе найденных заметок.
        # Приоритет у активной версии промпта из prompt_history (её можно
        # редактировать и откатывать), иначе используется встроенный промпт.
        default_system_prompt = (
            "Вы — ассистент базы знаний. Отвечайте на вопрос пользователя, "
            "используя ТОЛЬКО предоставленные ниже заметки. Отвечайте на русском языке. "
            "Обязательно ссылайтесь на конкретные заметки (указывайте их ID и названия). "
            "Если информации в заметках недостаточно для ответа — честно скажите: "
            "'В базе недостаточно данных'. Не придумывайте фактов."
        )
        system_prompt = get_active_system_prompt(self.state) or default_system_prompt
        user_prompt = (
            f"Вопрос пользователя: {query}\n\n"
            f"Найденные заметки:\n{context}\n\n"
            f"Напишите ответ со ссылками на заметки (указывайте их названия и ID)."
        )

        llm_response = self.llm.complete(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
        )

        if llm_response.error:
            logger.error("LLM ошибка при ответе на вопрос: %s", llm_response.error)
            response.enough_data = False
            response.answer = f"Ошибка при формировании ответа: {llm_response.error}"
            return response

        response.answer = llm_response.text
        response.sources = sources
        response.enough_data = True
        return response


def format_search_response(question: str, response: SearchResponse) -> str:
    """Форматирует результат поиска в текст для CLI (без повторного вызова LLM)."""
    lines: list[str] = []
    lines.append(f"## Вопрос: {question}")
    lines.append("")
    lines.append(response.answer or "В базе недостаточно данных.")
    lines.append("")
    if response.sources:
        lines.append("## Источники:")
        for src in response.sources:
            lines.append(f"- {src}")
    if not response.enough_data:
        lines.append("")
        lines.append("> В базе недостаточно данных для ответа на этот вопрос.")

    return "\n".join(lines)


def ask_question(config: AppConfig, state: StateManager, question: str) -> str:
    """Задаёт вопрос базе знаний и возвращает ответ с ссылками."""
    llm = LLMClient(config)
    engine = SearchEngine(config, state, llm)
    response = engine.search(question)
    return format_search_response(question, response)
