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


class TestStateSearchProvenance:
    """P2-05: search() возвращает поля provenance (URL, источник, путь, ревизия)."""

    def test_search_returns_provenance_fields(self, config, state):
        """Результат поиска содержит canonical_url, source_id, file_path, content_hash, revision."""
        state.index_note(
            note_id="note-prov-1",
            title="Заголовок про ИИ",
            tags="ии",
            topic="Технологии",
            content="Текст про нейросети и машинное обучение",
            source_id="tg_test",
            external_id="yt_vid1",
            canonical_url="https://t.me/s/test/42",
            file_path="/vault/note.md",
            content_hash="abc123",
            revision=1,
        )

        results = state.search("нейросети", limit=5)
        assert len(results) == 1
        row = results[0]
        assert row["note_id"] == "note-prov-1"
        assert row["canonical_url"] == "https://t.me/s/test/42"
        assert row["source_id"] == "tg_test"
        assert row["external_id"] == "yt_vid1"
        assert row["file_path"] == "/vault/note.md"
        assert row["content_hash"] == "abc123"
        assert row["revision"] == 1
        assert row["title"] == "Заголовок про ИИ"

    def test_search_missing_notes_row_returns_nulls(self, config, state):
        """Если записи в notes нет (только FTS), поля provenance — NULL."""
        conn = state.connect()
        conn.execute(
            "INSERT INTO notes_fts (note_id, title, tags, topic, content) VALUES (?, ?, ?, ?, ?)",
            ("note-fts-only", "Заголовок", "тэг", "Тема", "текст про поиск"),
        )
        conn.commit()

        results = state.search("поиск", limit=5)
        assert len(results) == 1
        row = results[0]
        assert row["note_id"] == "note-fts-only"
        assert row["canonical_url"] is None
        assert row["source_id"] is None
        assert row["file_path"] is None
        assert row["content_hash"] is None
        assert row["revision"] is None
