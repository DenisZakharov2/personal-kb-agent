"""Тесты поиска (P2-11): score из ранга FTS5, релевантный фрагмент (snippet)."""

from __future__ import annotations

from unittest.mock import MagicMock

from kb_agent.core.search import SearchEngine
from kb_agent.processing.llm_client import LLMClient


def _index_note(state, note_id, title, topic, content):
    state.index_note(
        note_id=note_id,
        title=title,
        tags="",
        topic=topic,
        content=content,
    )


def _make_llm():
    """LLMClient-заглушка, не ходящая в сеть."""
    llm = LLMClient.__new__(LLMClient)
    llm.complete = MagicMock(return_value=MagicMock(error=None, text="ответ"))
    return llm


class TestSearchScoreSnippet:
    def test_state_search_returns_rank(self, config, state):
        """state.search возвращает колонку rank для ранжирования результатов."""
        _index_note(
            state,
            "note-1",
            "Первая",
            "Тема",
            "Нейросети и машинное обучение меняют мир",
        )
        _index_note(
            state,
            "note-2",
            "Вторая",
            "Тема",
            "Купить хлеб и молоко в магазине",
        )

        results = state.search("нейросети", limit=5)
        assert len(results) >= 1
        note_ids = {r["note_id"] for r in results}
        assert "note-1" in note_ids
        # ранги присутствуют и являются числами
        for row in results:
            assert isinstance(row.get("rank"), (int, float)), row

    def test_search_snippet_contains_match(self, config, state):
        """snippet содержит фрагмент вокруг совпадения запроса."""
        _index_note(
            state,
            "note-snip",
            "Статья",
            "Тема",
            "Введение. Здесь говорится про нейросети и глубокое обучение. Заключение.",
        )

        engine = SearchEngine(config, state, _make_llm())
        response = engine.search("нейросети", limit=5)

        assert len(response.results) == 1
        r = response.results[0]
        assert r.snippet, "snippet не должен быть пустым"
        assert "нейросети" in r.snippet.lower()

    def test_search_engine_populates_score_and_snippet(self, config, state):
        """SearchEngine.search заполняет score (в [0,1]) и snippet в SearchResult."""
        _index_note(
            state,
            "note-se",
            "Заметка про ИИ",
            "Технологии",
            "Искусственный интеллект и нейросети — современные технологии.",
        )

        engine = SearchEngine(config, state, _make_llm())
        response = engine.search("искусственный интеллект", limit=5)

        assert len(response.results) == 1
        r = response.results[0]
        assert 0.0 <= r.score <= 1.0
        assert r.snippet, "snippet не должен быть пустым для совпадающей заметки"
