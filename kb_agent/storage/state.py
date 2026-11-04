"""Управление состоянием приложения в SQLite (Этап 1).

Единственное изменяемое хранилище — один SQLite-файл.
Таблицы: raw_queue, notes_fts (FTS5), note_links, feedback, prompt_history, processed,
pending_changes (Этап 4), sources (Этап 4).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

_SCHEMA = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS raw_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            external_id TEXT NOT NULL,
            canonical_url TEXT,
            title TEXT,
            raw_text TEXT,
            raw_hash TEXT UNIQUE,
            received_at TEXT NOT NULL DEFAULT (datetime('now')),
            state TEXT NOT NULL DEFAULT 'new' CHECK (state IN ('new','processing','processed','skipped','failed')),
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            next_retry_at TEXT,
            skipped_reason TEXT,
            UNIQUE(source_id, external_id)
        );
        """,
    ),
    (
        2,
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
            note_id UNINDEXED,
            title,
            tags,
            topic,
            content
        );
        """,
    ),
    (
        3,
        """
        CREATE TABLE IF NOT EXISTS note_links (
            source_note_id TEXT NOT NULL,
            target_note_id TEXT NOT NULL,
            link_type TEXT NOT NULL CHECK (link_type IN ('references','contradicts','updates','extends')),
            PRIMARY KEY (source_note_id, target_note_id, link_type)
        );
        """,
    ),
    (
        4,
        """
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            note_id TEXT NOT NULL,
            score INTEGER NOT NULL CHECK (score BETWEEN 1 AND 5),
            comment TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """,
    ),
    (
        5,
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
        """,
    ),
    (
        6,
        """
        CREATE TABLE IF NOT EXISTS processed (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            processed_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """,
    ),
    (
        7,
        """
        ALTER TABLE raw_queue ADD COLUMN published_at TEXT;
        """,
    ),
    (
        8,
        """
        CREATE TABLE IF NOT EXISTS pending_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            change_id TEXT UNIQUE NOT NULL,
            action TEXT NOT NULL CHECK (action IN ('add_source','remove_source','enable_source','update_profile','revert_prompt')),
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            parameters TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','confirmed','applied','rejected','failed')),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            confirmed_at TEXT,
            applied_at TEXT,
            confirmed_by TEXT,
            result TEXT
        );
        """,
    ),
    (
        9,
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
        """,
    ),
    (
        10,
        """
        -- Разрешить score=0 для общих сообщений обратной связи (без оценки)
        CREATE TABLE IF NOT EXISTS feedback_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            note_id TEXT NOT NULL,
            score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 5),
            comment TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        INSERT INTO feedback_new SELECT * FROM feedback;
        DROP TABLE feedback;
        ALTER TABLE feedback_new RENAME TO feedback;
        """,
    ),
]

SCHEMA_VERSION = max(v for v, _ in _SCHEMA)


class StateManager:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "StateManager":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_obj, exc_tb) -> None:
        self.close()

    def init_db(self) -> int:
        conn = self.connect()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY
            );
            """
        )
        conn.commit()
        row = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1").fetchone()
        current_version = row["version"] if row else 0
        for version, sql in _SCHEMA:
            if version > current_version:
                conn.executescript(sql)
                conn.execute(
                    "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
                    (version,),
                )
                conn.commit()
                current_version = version
        return current_version

    def enqueue(
        self,
        source_id: str,
        external_id: str,
        canonical_url: str,
        title: str,
        raw_text: str,
        raw_hash: str,
        published_at: Optional[str] = None,
    ) -> Optional[int]:
        """Добавляет элемент в очередь. Возвращает ID новой записи или None, если уже существует."""
        conn = self.connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO raw_queue (source_id, external_id, canonical_url, title, raw_text, raw_hash, published_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (source_id, external_id, canonical_url, title, raw_text, raw_hash, published_at),
            )
            conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            # Запись уже существует (по external_id или raw_hash)
            conn.rollback()
            return None

    def get_next_new(self) -> Optional[dict]:
        conn = self.connect()
        row = conn.execute(
            """
            SELECT * FROM raw_queue
            WHERE state = 'new'
            ORDER BY received_at ASC
            LIMIT 1
            """
        ).fetchone()
        return dict(row) if row else None

    def mark_processing(self, queue_id: int) -> None:
        conn = self.connect()
        conn.execute(
            "UPDATE raw_queue SET state = 'processing', attempts = attempts + 1 WHERE id = ?",
            (queue_id,),
        )
        conn.commit()

    def mark_processed(self, queue_id: int) -> None:
        conn = self.connect()
        conn.execute("UPDATE raw_queue SET state = 'processed' WHERE id = ?", (queue_id,))
        conn.commit()

    def mark_skipped(self, queue_id: int, reason: str) -> None:
        conn = self.connect()
        conn.execute(
            "UPDATE raw_queue SET state = 'skipped', skipped_reason = ? WHERE id = ?",
            (reason, queue_id),
        )
        conn.commit()

    def mark_failed(self, queue_id: int, error: str, next_retry_at: Optional[str] = None) -> None:
        conn = self.connect()
        conn.execute(
            "UPDATE raw_queue SET state = 'failed', last_error = ?, next_retry_at = ? WHERE id = ?",
            (error, next_retry_at, queue_id),
        )
        conn.commit()

    def index_note(self, note_id: str, title: str, tags: str, topic: str, content: str) -> None:
        conn = self.connect()
        conn.execute(
            """
            INSERT OR REPLACE INTO notes_fts (note_id, title, tags, topic, content)
            VALUES (?, ?, ?, ?, ?)
            """,
            (note_id, title, tags, topic, content),
        )
        conn.commit()

    def remove_from_index(self, note_id: str) -> None:
        conn = self.connect()
        conn.execute("DELETE FROM notes_fts WHERE note_id = ?", (note_id,))
        conn.commit()

    def search(self, query: str, limit: int = 10) -> list[dict]:
        conn = self.connect()
        # Экранируем запрос для FTS5: разбиваем на слова и соединяем через OR
        # (чтобы найти заметки по любому из слов)
        words = [w.strip() for w in query.split() if w.strip()]
        if not words:
            return []
        fts_query = " OR ".join(f'"{w}"' for w in words)
        rows = conn.execute(
            """
            SELECT note_id, title, tags, topic, content
            FROM notes_fts
            WHERE notes_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def add_link(self, source_note_id: str, target_note_id: str, link_type: str) -> None:
        conn = self.connect()
        conn.execute(
            """
            INSERT OR IGNORE INTO note_links (source_note_id, target_note_id, link_type)
            VALUES (?, ?, ?)
            """,
            (source_note_id, target_note_id, link_type),
        )
        conn.commit()

    def add_feedback(self, note_id: str, score: int, comment: str) -> int:
        conn = self.connect()
        cur = conn.execute(
            """
            INSERT INTO feedback (note_id, score, comment)
            VALUES (?, ?, ?)
            """,
            (note_id, score, comment),
        )
        conn.commit()
        return cur.lastrowid

    def save_prompt(
        self,
        prompt_name: str,
        version: int,
        content: str,
        is_active: bool = False,
        approved_by: Optional[str] = None,
        reverted_from: Optional[int] = None,
    ) -> int:
        conn = self.connect()
        cur = conn.execute(
            """
            INSERT INTO prompt_history (prompt_name, version, content, is_active, approved_by, reverted_from)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (prompt_name, version, content, int(is_active), approved_by, reverted_from),
        )
        conn.commit()
        return cur.lastrowid

    def get_active_prompt(self, prompt_name: str) -> Optional[dict]:
        conn = self.connect()
        row = conn.execute(
            """
            SELECT * FROM prompt_history
            WHERE prompt_name = ? AND is_active = 1
            ORDER BY version DESC LIMIT 1
            """,
            (prompt_name,),
        ).fetchone()
        return dict(row) if row else None

    def is_processed(self, key: str) -> bool:
        conn = self.connect()
        row = conn.execute("SELECT 1 FROM processed WHERE key = ?", (key,)).fetchone()
        return row is not None

    def mark_processed_key(self, key: str) -> None:
        conn = self.connect()
        conn.execute("INSERT OR IGNORE INTO processed (key) VALUES (?)", (key,))
        conn.commit()

    # ---- Управление источниками (Этап 4, write-операции) ----

    def add_source(
        self,
        source_id: str,
        source_type: str,
        channel: str,
        enabled: bool = True,
    ) -> Optional[int]:
        """Добавляет новый источник. Возвращает ID записи или None, если уже существует."""
        conn = self.connect()
        try:
            cur = conn.execute(
                """
                INSERT INTO sources (source_id, source_type, channel, enabled)
                VALUES (?, ?, ?, ?)
                """,
                (source_id, source_type, channel, int(enabled)),
            )
            conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            conn.rollback()
            return None

    def get_source(self, source_id: str) -> Optional[dict]:
        conn = self.connect()
        row = conn.execute("SELECT * FROM sources WHERE source_id = ?", (source_id,)).fetchone()
        return dict(row) if row else None

    def list_sources(self, enabled_only: bool = False) -> list[dict]:
        conn = self.connect()
        if enabled_only:
            rows = conn.execute(
                "SELECT * FROM sources WHERE enabled = 1 ORDER BY added_at"
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM sources ORDER BY added_at").fetchall()
        return [dict(row) for row in rows]

    def set_source_enabled(self, source_id: str, enabled: bool) -> bool:
        conn = self.connect()
        cur = conn.execute(
            """
            UPDATE sources
            SET enabled = ?, updated_at = datetime('now')
            WHERE source_id = ?
            """,
            (int(enabled), source_id),
        )
        conn.commit()
        return cur.rowcount > 0

    def remove_source(self, source_id: str) -> bool:
        conn = self.connect()
        cur = conn.execute("DELETE FROM sources WHERE source_id = ?", (source_id,))
        conn.commit()
        return cur.rowcount > 0

    def stats(self) -> dict:
        conn = self.connect()
        result: dict = {}
        for table in ("raw_queue", "feedback", "prompt_history", "processed", "sources", "pending_changes"):
            row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
            result[table] = row["c"] if row else 0
        row = conn.execute("SELECT state, COUNT(*) AS c FROM raw_queue GROUP BY state").fetchall()
        result["queue_by_state"] = {r["state"]: r["c"] for r in row}
        return result
