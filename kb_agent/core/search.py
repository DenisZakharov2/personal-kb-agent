"""Поисковая система базы знаний (Этап 3).

Поиск через SQLite FTS5 + LLM-синтез ответа со ссылками на заметки.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from kb_agent.config import AppConfig
from kb_agent.core.prompts import (
    ANSWER_PROMPT_NAME,
    DEFAULT_PROMPT_NAME,
    get_active_system_prompt,
)
from kb_agent.processing.llm_client import LLMClient
from kb_agent.storage.state import StateManager, _WORD_RE
from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.core.search")

# Окно релевантного фрагмента (в символах) вокруг совпадения, используемое
# в контексте LLM вместо первых 1500 символов заметки (P2-11).
SNIPPET_WINDOW = 240

# Размер чанка (в символах) при разбиении длинного контента и перекрытие
# между соседними чанками, чтобы цитата не обрывалась mid-sentence.
CHUNK_TARGET_SIZE = 1500
CHUNK_OVERLAP = 200


@dataclass
class SearchResult:
    """Результат поиска одной заметки."""

    note_id: str
    title: str
    tags: str
    topic: str
    content: str
    score: float = 0.0
    # Релевантный фрагмент вокруг совпадения запроса (P2-11).
    snippet: str = ""
    # Смещение фрагмента в исходном контенте (start, end) для цитирования.
    snippet_offset: Optional[tuple[int, int]] = None
    # Поля provenience (из таблицы notes): источник, путь, версия и hash.
    canonical_url: Optional[str] = None
    source_id: Optional[str] = None
    external_id: Optional[str] = None
    file_path: Optional[str] = None
    content_hash: Optional[str] = None
    revision: Optional[int] = None
    # Метка приватности / чувствительности (6.2)
    is_private: bool = False
    sensitivity: str = "public"


@dataclass
class Chunk:
    """Один фрагмент контента со смещениями для цитирования."""

    text: str
    start: int
    end: int


def chunk_text(
    text: str, target_size: int = CHUNK_TARGET_SIZE, overlap: int = CHUNK_OVERLAP
) -> list[Chunk]:
    """Разбивает длинный контент на чанки с сохранением смещений (offsets).

    Каждый чанк содержит ``text`` и смещения ``start``/``end`` относительно
    исходного текста, что позволяет цитировать фрагмент со ссылкой на место
    в источнике. Перекрытие между чанками не даёт обрывать цитаты
    посередине предложения.
    """
    text = text or ""
    n = len(text)
    if n == 0:
        return []
    chunks: list[Chunk] = []
    start = 0
    while start < n:
        end = min(n, start + target_size)
        if end < n:
            # Постараться оборвать чанк на границе абзаца/предложения.
            cut = end
            for sep in ("\n\n", ". ", "! ", "? ", "。", " ", ""):
                idx = text.rfind(sep, start, end)
                if idx != -1:
                    cut = idx + len(sep) if sep else idx
                    break
            end = max(cut, start + 1)
        chunks.append(Chunk(text=text[start:end], start=start, end=end))
        if end >= n:
            break
        start = end - overlap
        if start <= chunks[-1].start:
            start = chunks[-1].end
    return chunks


def _make_snippet(
    content: str, query_terms: list[str], window: int = SNIPPET_WINDOW
) -> tuple[str, Optional[tuple[int, int]]]:
    """Возвращает (фрагмент, (start, end)) вокруг первого совпадения запроса.

    Если ни один термин не найден — возвращает начало контента.
    Окружает фрагмент многоточиями, если он обрезан по краям.
    """
    if not content or not query_terms:
        snippet = content[:window] if content else ""
        return snippet, (0, len(snippet)) if snippet else None
    lower = content.lower()
    best_pos: Optional[int] = None
    for term in query_terms:
        pos = lower.find(term.lower())
        if pos != -1 and (best_pos is None or pos < best_pos):
            best_pos = pos
    if best_pos is None:
        snippet = content[:window]
        return snippet, (0, len(snippet)) if snippet else None
    start = max(0, best_pos - window // 2)
    end = min(len(content), best_pos + window // 2)
    snippet = content[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(content):
        snippet = snippet + "…"
    return snippet.strip(), (start, end)


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

    def search(
        self,
        query: str,
        limit: int = 5,
        *,
        source_id: Optional[str] = None,
        topic: Optional[str] = None,
        tags: Optional[list[str]] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        include_archived: bool = False,
    ) -> SearchResponse:
        """Ищет заметки по запросу с фильтрами и возвращает релевантные результаты.

        Фильтры: ``source_id``, ``topic``, ``tags`` (список, все должны
        совпасть), ``date_from``/``date_to`` (по дате создания заметки).
        Архивные заметки исключаются по умолчанию.
        """
        logger.info("Поиск: %s", query)

        # FTS5 поиск с фильтрами по метаданным (provenance).
        rows = self.state.search(
            query,
            limit=limit,
            source_id=source_id,
            topic=topic,
            tags=tags,
            date_from=date_from,
            date_to=date_to,
            include_archived=include_archived,
        )
        query_terms = _WORD_RE.findall(query or "")
        results: list[SearchResult] = []
        best_rank: Optional[float] = None
        for row in rows:
            rank = row.get("rank")
            if isinstance(rank, (int, float)):
                best_rank = rank if best_rank is None else min(best_rank, rank)
            # FTS5 rank: чем меньше, тем лучше. Переводим в score 0.0-1.0,
            # где лучше ранга выше score. Лучший результат в выдаче -> ~1.0.
            score = max(0.0, min(1.0, 1.0 + (rank or 0.0)))
            content = row.get("content", "") or ""
            snippet, snippet_offset = _make_snippet(content, query_terms)
            results.append(
                SearchResult(
                    note_id=row["note_id"],
                    title=row["title"] or "Без названия",
                    tags=row["tags"] or "",
                    topic=row["topic"] or "",
                    content=content,
                    score=score,
                    snippet=snippet,
                    snippet_offset=snippet_offset,
                    canonical_url=row.get("canonical_url"),
                    source_id=row.get("source_id"),
                    external_id=row.get("external_id"),
                    file_path=row.get("file_path"),
                    content_hash=row.get("content_hash"),
                    revision=row.get("revision"),
                    is_private=bool(row.get("is_private", 0)),
                    sensitivity="private" if row.get("is_private") else "public",
                )
            )

        response = SearchResponse(query=query, results=results)

        if not results:
            response.enough_data = False
            response.answer = "В базе недостаточно данных по вашему запросу."
            return response

        # Формируем контекст для LLM из топ-N заметок (релевантный фрагмент,
        # а не первые 1500 символов — P2-11)
        context_parts: list[str] = []
        sources: list[str] = []
        for r in results:
            context_text = r.snippet or r.content[:SNIPPET_WINDOW]
            context_parts.append(
                f"--- Заметка: {r.title} (ID: {r.note_id}, теги: {r.tags}, score: {r.score:.2f}) ---\n{context_text}"
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

        # Метаданные генерации ответа (prompt_type = search).
        active_prompt = self.state.get_active_prompt(
            DEFAULT_PROMPT_NAME, prompt_type="search"
        )
        self.state.save_generation_log(
            note_id=None,
            operation="answer",
            model=getattr(self.llm, "model", "unknown"),
            prompt_name=DEFAULT_PROMPT_NAME,
            prompt_version=active_prompt["version"] if active_prompt else None,
            prompt_tokens=llm_response.prompt_tokens,
            completion_tokens=llm_response.completion_tokens,
            latency_ms=llm_response.latency_ms,
            cost_usd=0.0,
        )
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


@dataclass
class SearchAnswerValidation:
    """Результат валидации ответа поиска (structured output для ответа LLM)."""

    passed: bool
    cited_note_ids: list[str] = field(default_factory=list)
    unknown_citations: list[str] = field(default_factory=list)
    forbidden_phrases_found: list[str] = field(default_factory=list)
    reason: str = ""


def _extract_cited_ids(text: str) -> list[str]:
    """Извлекает упоминания ID заметок вида 'ID: <note_id>' из текста ответа."""
    import re as _re

    ids: list[str] = []
    for match in _re.finditer(r"ID:\s*([A-Za-z0-9_\-:.]+)", text or ""):
        ids.append(match.group(1))
    return ids


def validate_search_answer(
    answer: str,
    results: list[SearchResult],
    forbidden_phrases: Optional[list[str]] = None,
) -> SearchAnswerValidation:
    """Валидация структуры ответа поиска (валидация вывода LLM).

    Проверяет:
    - все упомянутые ID заметок ссылаются на реальные результаты выдачи
      (нет выдуманных ссылок);
    - в ответе нет запрещённых фраз/галюцинаций;
    - если ответ не «недостаточно данных», хоть одна заметка процитирована.
    """
    known_ids = {r.note_id for r in results}
    cited = _extract_cited_ids(answer or "")
    unknown = [cid for cid in cited if cid not in known_ids]
    forbidden_phrases = forbidden_phrases or []
    found_forbidden = [phrase for phrase in forbidden_phrases if phrase in (answer or "")]

    reason_parts: list[str] = []
    if unknown:
        reason_parts.append(f"выдуманные ссылки: {unknown}")
    if found_forbidden:
        reason_parts.append(f"запрещённые фразы: {found_forbidden}")
    if not cited and known_ids:
        reason_parts.append("нет цитат из найденных заметок")

    passed = bool(
        not unknown and not found_forbidden and (cited or not known_ids)
    )
    return SearchAnswerValidation(
        passed=passed,
        cited_note_ids=[cid for cid in cited if cid in known_ids],
        unknown_citations=unknown,
        forbidden_phrases_found=found_forbidden,
        reason="; ".join(reason_parts) if reason_parts else "ответ валиден",
    )


def ask_question(config: AppConfig, state: StateManager, question: str) -> str:
    """Задаёт вопрос базе знаний и возвращает ответ с ссылками."""
    llm = LLMClient(config)
    engine = SearchEngine(config, state, llm)
    response = engine.search(question)
    return format_search_response(question, response)
