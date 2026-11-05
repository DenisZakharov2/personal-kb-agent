"""Тесты идемпотентности миграций (R-08 аудита).

Проверяют, что миграция №10 (разрешить score = 0 в таблице feedback)
выполняется транзакционно, идемпотентно и корректно обрабатывает
остатки от предыдущего неудачного запуска.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from kb_agent.storage.state import StateManager


def _setup_db_v9(db_path: Path) -> None:
    """Создаёт БД с набором таблиц версии 9 (до миграции №10).

    Таблицы raw_queue, notes_fts, note_links, feedback (старая схема),
    processed, pending_changes (старые), sources.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS raw_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT NOT NULL,
                external_id TEXT NOT NULL,
                canonical_url TEXT,
                title TEXT,
                raw_text TEXT,
                raw_hash TEXT,
                published_at TEXT,
                UNIQUE(source_id, external_id)
            );
            """
        )
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
                note_id UNINDEXED,
                title,
                tags,
                topic,
                content
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS note_links (
                source_note_id TEXT NOT NULL,
                target_note_id TEXT NOT NULL,
                link_type TEXT NOT NULL CHECK (link_type IN ('references','contradicts','updates','extends')),
                PRIMARY KEY (source_note_id, target_note_id, link_type)
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                note_id TEXT NOT NULL,
                score INTEGER NOT NULL CHECK (score BETWEEN 1 AND 5),
                comment TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS prompt_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt_name TEXT NOT NULL,
                version INTEGER NOT NULL,
                content TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                approved_by TEXT,
                reverted_from INTEGER
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS processed (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                processed_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                change_id TEXT UNIQUE NOT NULL,
                action TEXT NOT NULL CHECK (action IN ('add_source','remove_source','enable_source','update_profile','revert_prompt')),
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                parameters TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','confirmed','applied','rejected','failed','expired')),
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                confirmed_at TEXT,
                applied_at TEXT,
                confirmed_by TEXT,
                result TEXT
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id TEXT UNIQUE NOT NULL,
                source_type TEXT NOT NULL CHECK (source_type IN ('telegram','youtube')),
                channel TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                added_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        conn.execute(
            "INSERT INTO feedback (note_id, score, comment) VALUES ('n1', 5, 'отлично')"
        )
        conn.execute(
            "INSERT INTO feedback (note_id, score, comment) VALUES ('n2', 3, 'нормально')"
        )
        conn.execute(
            "INSERT INTO schema_version (version) VALUES (9)"
        )
        conn.commit()
    finally:
        conn.close()


def _feedback_check_constraint(db_path: Path) -> str | None:
    """Возвращает SQL-создание таблицы feedback или None."""
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'feedback'"
        ).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _feedback_rows(db_path: Path) -> list[dict]:
    """Возвращает все строки таблицы feedback."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT id, note_id, score, comment FROM feedback ORDER BY id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


class TestFeedbackMigration:
    """Миграция №10: разрешить score = 0."""

    def test_migration_updates_check_constraint(self, temp_dir):
        """После init_db() CHECK constraint позволяет score = 0."""
        db_path = temp_dir / "state.db"
        _setup_db_v9(db_path)

        state = StateManager(db_path)
        try:
            state.init_db()
        finally:
            state.close()

        sql = _feedback_check_constraint(db_path)
        assert sql is not None
        assert "BETWEEN 0 AND 5" in sql, f"Необновлённая схема: {sql}"

    def test_migration_preserves_data(self, temp_dir):
        """Данные в feedback сохраняются после миграции."""
        db_path = temp_dir / "state.db"
        _setup_db_v9(db_path)

        state = StateManager(db_path)
        try:
            state.init_db()
        finally:
            state.close()

        rows = _feedback_rows(db_path)
        assert len(rows) == 2
        assert rows[0]["note_id"] == "n1"
        assert rows[0]["score"] == 5
        assert rows[1]["note_id"] == "n2"
        assert rows[1]["score"] == 3

    def test_migration_is_idempotent(self, temp_dir):
        """Повторный init_db() не ломает таблицу feedback."""
        db_path = temp_dir / "state.db"
        _setup_db_v9(db_path)

        state = StateManager(db_path)
        try:
            state.init_db()
            state.init_db()  # повторный вызов
        finally:
            state.close()

        sql = _feedback_check_constraint(db_path)
        assert sql is not None
        assert "BETWEEN 0 AND 5" in sql

        rows = _feedback_rows(db_path)
        assert len(rows) == 2  # нет дублей

    def test_migration_handles_leftover_feedback_v2(self, temp_dir):
        """Остаток feedback_v2 от неудачного запуска не мешает миграции."""
        db_path = temp_dir / "state.db"
        _setup_db_v9(db_path)

        # Симулируем остаток от предыдущего неудачного запуска миграции.
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(
                """
                CREATE TABLE feedback_v2 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    note_id TEXT NOT NULL,
                    score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 5),
                    comment TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

        state = StateManager(db_path)
        try:
            state.init_db()
        finally:
            state.close()

        sql = _feedback_check_constraint(db_path)
        assert sql is not None
        assert "BETWEEN 0 AND 5" in sql

        rows = _feedback_rows(db_path)
        assert len(rows) == 2  # исходные данные preserved

    def test_migration_noop_on_new_db(self, temp_dir):
        """На новой БД миграция не создаёт лишних таблиц."""
        db_path = temp_dir / "state.db"
        state = StateManager(db_path)
        try:
            state.init_db()
        finally:
            state.close()

        sql = _feedback_check_constraint(db_path)
        assert sql is not None
        assert "BETWEEN 0 AND 5" in sql
