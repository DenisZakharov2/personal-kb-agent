"""Тесты дедупликации FTS (P2-04)."""

from __future__ import annotations

from kb_agent.storage.state import StateManager


class TestFtsDedup:
    """P2-04: Повторная индексация одного note_id оставляет один результат."""

    def test_index_note_no_duplicates(self, state):
        """Два вызова index_note с одинаковым note_id — одна строка в FTS."""
        state.index_note(
            note_id="note_1",
            title="Title 1",
            tags="test",
            topic="Тема",
            content="some content here",
        )
        state.index_note(
            note_id="note_1",
            title="Title 1 updated",
            tags="test, updated",
            topic="Тема",
            content="updated content here",
        )

        results = state.search("content", limit=10)
        # Должен быть ровно 1 результат, а не 2
        assert len(results) == 1
        assert results[0]["title"] == "Title 1 updated"

    def test_search_returns_unique_note_ids(self, state):
        """Поиск не возвращает дубликатов note_id."""
        state.index_note("n1", "A", "t1", "topic", "foo bar")
        state.index_note("n2", "B", "t2", "topic", "foo baz")
        state.index_note("n1", "A updated", "t1", "topic", "foo bar updated")

        results = state.search("foo", limit=10)
        note_ids = [r["note_id"] for r in results]
        assert len(note_ids) == len(set(note_ids)), f"Дубликаты note_id: {note_ids}"