"""Тесты фильтров поиска, чанкинга и валидации ответов (раздел 9 аудита)."""

from __future__ import annotations

from unittest.mock import MagicMock

from kb_agent.core.search import (
    SearchEngine,
    SearchResult,
    chunk_text,
    validate_search_answer,
)
from kb_agent.processing.llm_client import LLMClient


def _index_note(state, note_id, title, topic, tags, source_id, content):
    state.index_note(
        note_id=note_id,
        title=title,
        tags=tags,
        topic=topic,
        content=content,
        source_id=source_id,
        external_id=f"ext-{note_id}",
        canonical_url=f"https://example.com/{note_id}",
        file_path=f"/vault/{topic}/{note_id}.md",
        content_hash="hash",
        revision=1,
    )


def _make_llm(text="ответ"):
    llm = LLMClient.__new__(LLMClient)
    llm.complete = MagicMock(return_value=MagicMock(error=None, text=text))
    return llm


class TestSearchFilters:
    def test_filter_by_source_id(self, config, state):
        _index_note(state, "n1", "T1", "Тема", "тэг1", "tg_chan", "нейросети в телеграме")
        _index_note(state, "n2", "T2", "Тема", "тэг2", "yt_chan", "нейросети на ютубе")

        results = state.search("нейросети", limit=10, source_id="tg_chan")
        assert len(results) == 1
        assert results[0]["source_id"] == "tg_chan"

    def test_filter_by_topic(self, config, state):
        _index_note(state, "n1", "T1", "ИИ", "тэг", "tg_chan", "нейросети")
        _index_note(state, "n2", "T2", "Автоматизация", "тэг", "tg_chan", "скрипты")

        results = state.search("нейросети", limit=10, topic="ИИ")
        assert len(results) == 1
        assert results[0]["topic"] == "ИИ"

    def test_filter_by_tags(self, config, state):
        _index_note(state, "n1", "T1", "Тема", "тэг1,тэг2", "tg_chan", "нейросети")
        _index_note(state, "n2", "T2", "Тема", "тэг3", "tg_chan", "нейросети")

        results = state.search("нейросети", limit=10, tags=["тэг1"])
        assert len(results) == 1
        assert results[0]["note_id"] == "n1"

    def test_filter_date_range(self, config, state):
        _index_note(state, "n1", "T1", "Тема", "тэг", "tg_chan", "старая заметка")
        _index_note(state, "n2", "T2", "Тема", "тэг", "tg_chan", "новая заметка")
        # Установим разные даты через прямое обновление
        conn = state.connect()
        conn.execute(
            "UPDATE notes SET created_at = '2025-01-01T00:00:00+00:00' WHERE note_id = 'n1'"
        )
        conn.execute(
            "UPDATE notes SET created_at = '2026-01-01T00:00:00+00:00' WHERE note_id = 'n2'"
        )
        conn.commit()

        results = state.search("заметка", limit=10, date_from="2026-01-01")
        assert len(results) == 1
        assert results[0]["note_id"] == "n2"

    def test_include_archived_excludes_by_default(self, config, state):
        _index_note(state, "n1", "Актуальная", "Тема", "тэг", "tg_chan", "контент актуальной")
        _index_note(state, "n2", "Архивная", "Тема", "тэг", "tg_chan", "контент архивной")
        state.set_note_archived("n2", True)

        default_results = state.search("контент", limit=10)
        assert len(default_results) == 1
        assert default_results[0]["note_id"] == "n1"

        archived_results = state.search("контент", limit=10, include_archived=True)
        assert len(archived_results) == 2


class TestChunking:
    def test_chunk_text_returns_offsets(self):
        text = "Абзац один. " * 200
        chunks = chunk_text(text, target_size=200, overlap=20)
        assert len(chunks) > 1
        for ch in chunks:
            assert ch.start >= 0
            assert ch.end > ch.start
            assert ch.text == text[ch.start : ch.end]

    def test_chunk_text_overlap(self):
        text = "x" * 1000
        chunks = chunk_text(text, target_size=300, overlap=50)
        # Перекрытие: следующий чанк начинается до конца предыдущего
        for i in range(1, len(chunks)):
            assert chunks[i].start < chunks[i - 1].end

    def test_search_result_has_snippet_offset(self, config, state):
        _index_note(
            state, "n1", "T1", "Тема", "тэг", "tg_chan", "Введение. нейросети здесь. Заключение."
        )
        engine = SearchEngine(config, state, _make_llm())
        response = engine.search("нейросети", limit=5)
        assert len(response.results) == 1
        r = response.results[0]
        assert r.snippet_offset is not None
        start, end = r.snippet_offset
        assert "нейросети" in r.content[start:end]


class TestValidateSearchAnswer:
    def test_valid_answer_with_real_citation(self, config, state):
        _index_note(state, "n1", "T1", "Тема", "тэг", "tg_chan", "контент")
        results = [
            SearchResult(
                note_id="n1",
                title="T1",
                tags="тэг",
                topic="Тема",
                content="контент",
                score=1.0,
            )
        ]
        validation = validate_search_answer(
            "Ответ по заметке ID: n1", results, forbidden_phrases=["выдумка"]
        )
        assert validation.passed is True
        assert validation.cited_note_ids == ["n1"]
        assert validation.unknown_citations == []

    def test_invalid_answer_with_fake_citation(self, config, state):
        _index_note(state, "n1", "T1", "Тема", "тэг", "tg_chan", "контент")
        results = [
            SearchResult(
                note_id="n1", title="T1", tags="тэг", topic="Тема", content="контент", score=1.0
            )
        ]
        validation = validate_search_answer("См. вымышленную заметку ID: fake-123", results)
        assert validation.passed is False
        assert "fake-123" in validation.unknown_citations

    def test_invalid_answer_with_forbidden_phrase(self, config, state):
        _index_note(state, "n1", "T1", "Тема", "тэг", "tg_chan", "контент")
        results = [
            SearchResult(
                note_id="n1", title="T1", tags="тэг", topic="Тема", content="контент", score=1.0
            )
        ]
        validation = validate_search_answer(
            "Ответ ID: n1. Секретный проект запущен.",
            results,
            forbidden_phrases=["Секретный проект"],
        )
        assert validation.passed is False
        assert "Секретный проект" in validation.forbidden_phrases_found
