"""P2-02: атомарность и конкурентность StateManager (WAL, busy_timeout)."""

from __future__ import annotations

import pytest

from kb_agent.storage.state import StateManager


class TestStateConnectPragmas:
    def test_connect_sets_wal_and_busy_timeout(self, config, state):
        """После connect() режим журналирования WAL и busy_timeout установлены."""
        cur = state.connect().execute("PRAGMA journal_mode")
        assert cur.fetchone()[0].lower() == "wal"

        cur = state.connect().execute("PRAGMA busy_timeout")
        assert cur.fetchone()[0] > 0


class TestStateIndexNoteDedup:
    def test_reindex_same_note_no_fts_duplicates(self, config, state):
        """Повторная индексация одного note_id не создаёт дублей в notes_fts."""
        state.index_note(
            note_id="note-1",
            title="Заголовок",
            tags="тэг1,тэг2",
            topic="Тема",
            content="Содержимое заметки для индексации",
        )
        state.index_note(
            note_id="note-1",
            title="Заголовок обновлён",
            tags="тэг1,тэг2",
            topic="Тема",
            content="Обновлённое содержимое заметки",
        )

        conn = state.connect()
        rows = conn.execute(
            "SELECT COUNT(*) AS cnt FROM notes_fts WHERE note_id = ?", ("note-1",)
        ).fetchone()
        assert rows["cnt"] == 1, f"Ожидалась 1 строка FTS, получено {rows['cnt']}"

        notes_rows = conn.execute(
            "SELECT COUNT(*) AS cnt FROM notes WHERE note_id = ?", ("note-1",)
        ).fetchone()
        assert notes_rows["cnt"] == 1

    def test_rebuild_fts_restores_index_from_notes(self, config, state):
        """rebuild_fts перестраивает notes_fts из таблицы notes."""
        state.index_note(
            note_id="note-rebuild-1",
            title="Первая",
            tags="тэг1",
            topic="Тема",
            content="содержимое первой заметки",
        )
        state.index_note(
            note_id="note-rebuild-2",
            title="Вторая",
            tags="тэг2",
            topic="Тема",
            content="содержимое второй заметки",
        )

        # Очищаем FTS вручную, как после частичного сбоя
        conn = state.connect()
        conn.execute("DELETE FROM notes_fts")
        conn.commit()
        assert conn.execute("SELECT COUNT(*) FROM notes_fts").fetchone()[0] == 0

        count = state.rebuild_fts()
        assert count == 2

        rows = conn.execute(
            "SELECT COUNT(*) AS cnt FROM notes_fts"
        ).fetchone()
        assert rows["cnt"] == 2

    def test_revision_increments_on_reindex(self, config, state):
        """revision увеличивается при повторной индексации того же note_id."""
        state.index_note(
            note_id="note-2",
            title="T1",
            tags="t1",
            topic="Тема",
            content="C1",
        )
        state.index_note(
            note_id="note-2",
            title="T2",
            tags="t1",
            topic="Тема",
            content="C2",
        )

        conn = state.connect()
        row = conn.execute("SELECT revision FROM notes WHERE note_id = ?", ("note-2",)).fetchone()
        assert row is not None
        assert row["revision"] == 2
