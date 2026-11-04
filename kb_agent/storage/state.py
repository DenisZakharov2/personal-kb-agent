"""Управление состоянием приложения в SQLite (Этап 1).

Каноническое изменяемое хранилище — один SQLite-файл; также меняются файлы vault и sources.yaml.
Таблицы: raw_queue, notes_fts (FTS5), note_links, feedback, prompt_history, processed,
pending_changes (Этап 4), sources (Этап 4).
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional, Union

from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.storage.state")

# Токен для FTS5: буквы/цифры, допускается дефис внутри слова.
# За счёт этого кавычки, звёздочки и прочие операторы FTS5 в запрос не попадают.
_WORD_RE = re.compile(r"[^\W_]+(?:-[^\W_]+)*", re.UNICODE)


def _migrate_feedback_allow_zero(conn: sqlite3.Connection) -> None:
    """Разрешает score = 0 (сообщения обратной связи без оценки).

    Миграция идемпотентна: если таблица уже допускает 0, ничего не делает.
    Выполняется в одной транзакции, поэтому падение не оставляет БД
    в состоянии «feedback удалён, feedback_v2 не переименован».
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'feedback'"
    ).fetchone()
    if row and "BETWEEN 0 AND 5" in (row["sql"] or ""):
        return

    conn.commit()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS feedback_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                note_id TEXT NOT NULL,
                score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 5),
                comment TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        has_old_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'feedback'"
        ).fetchone()
        if has_old_table:
            conn.execute(
                """
                INSERT OR IGNORE INTO feedback_v2 (id, note_id, score, comment, created_at)
                SELECT id, note_id, score, comment, created_at FROM feedback
                """
            )
            conn.execute("DROP TABLE feedback")
        conn.execute("ALTER TABLE feedback_v2 RENAME TO feedback")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    logger.info("Миграция feedback: разрешён score = 0")


SchemaStep = Union[str, Callable[[sqlite3.Connection], None]]

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
        _migrate_feedback_allow_zero,
    ),
    (
        11,
        """
        ALTER TABLE raw_queue ADD COLUMN processing_started_at TEXT;
        ALTER TABLE raw_queue ADD COLUMN lease_expires_at TEXT;
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
        for version, step in _SCHEMA:
            if version > current_version:
                # Шаг может быть SQL-скриптом или функцией-миграцией (для шагов,
                # которым нужны проверки/транзакции).
                if callable(step):
                    step(conn)
                else:
                    conn.executescript(step)
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
            "UPDATE raw_queue SET state = 'failed', last_error = ?, next_retry_at = ?, lease_expires_at = NULL WHERE id = ?",
            (error, next_retry_at, queue_id),
        )
        conn.commit()

    def claim_next(
        self,
        lease_seconds: int = 300,
    ) -> Optional[dict]:
        """Атоматически захватывает задание для обработки.

        Выбирает элемент в состоянии 'new', либо 'processing'/'failed'
        с истекшим lease (lease_expires_at < now) либо failed с
        next_retry_at <= now. Возвращает запись или None.

        Использует BEGIN IMMEDIATE для защиты от параллельных воркеров.
        """
        conn = self.connect()
        now = datetime.now(timezone.utc).isoformat()

        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                """
                SELECT * FROM raw_queue
                WHERE (
                    state = 'new'
                    OR (state = 'processing' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?)
                    OR (state = 'failed' AND next_retry_at IS NOT NULL AND next_retry_at <= ?)
                )
                ORDER BY received_at ASC
                LIMIT 1
                """,
                (now, now),
            ).fetchone()

            if row is None:
                conn.rollback()
                return None

            queue_id = row["id"]
            lease_expires = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat()

            conn.execute(
                """
                UPDATE raw_queue
                SET state = 'processing',
                    attempts = attempts + 1,
                    processing_started_at = ?,
                    lease_expires_at = ?
                WHERE id = ?
                """,
                (now, lease_expires, queue_id),
            )
            conn.commit()
            return dict(row)
        except Exception:
            conn.rollback()
            raise

    def release_lease(self, queue_id: int) -> None:
        """Освобождает lease, возвращая элемент в состояние 'new' (если не был завершён)."""
        conn = self.connect()
        conn.execute(
            """
            UPDATE raw_queue
            SET state = 'new',
                processing_started_at = NULL,
                lease_expires_at = NULL
            WHERE id = ? AND state = 'processing'
            """,
            (queue_id,),
        )
        conn.commit()

    def index_note(self, note_id: str, title: str, tags: str, topic: str, content: str) -> None:
        conn = self.connect()
        conn.execute("DELETE FROM notes_fts WHERE note_id = ?", (note_id,))
        conn.execute(
            """
            INSERT INTO notes_fts (note_id, title, tags, topic, content)
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
        # Ограничиваем размер выдачи: защита от выкачивания всей базы в контекст LLM.
        try:
            limit = max(1, min(int(limit), 20))
        except (TypeError, ValueError):
            limit = 10

        # Экранируем запрос для FTS5: оставляем только слова, соединяем через OR
        # (чтобы найти заметки по любому из слов). Операторы FTS5 (", *, -, NEAR)
        # и прочий синтаксис в запрос не попадают.
        words = _WORD_RE.findall(query or "")
        if not words:
            return []
        fts_query = " OR ".join(f'"{word}"' for word in words)
        try:
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
        except sqlite3.OperationalError as exc:
            # Некорректный с точки зрения FTS5 запрос не должен ронять CLI/MCP.
            logger.warning("FTS-запрос отклонён (%s): %s", fts_query, exc)
            return []
        return [dict(row) for row in rows]

    def get_note(self, note_id: str) -> Optional[dict]:
        """Возвращает заметку из FTS5-индекса по её ID (или None)."""
        conn = self.connect()
        row = conn.execute(
            """
            SELECT note_id, title, tags, topic, content
            FROM notes_fts
            WHERE note_id = ?
            LIMIT 1
            """,
            (note_id,),
        ).fetchone()
        return dict(row) if row else None

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

    def seed_prompt_from_file(self, prompt_name: str, prompt_path: Path) -> Optional[dict]:
        """Заводит версию 1 промпта из файла, если активной версии ещё нет.

        Нужен, чтобы `prompt_history` не оставалась пустой: без этого
        `self-improve rollback` и MCP `kb_revert_prompt` нечего откатывать.
        """
        if self.get_active_prompt(prompt_name):
            return None
        prompt_path = Path(prompt_path)
        if not prompt_path.exists():
            logger.warning("Файл промпта не найден: %s", prompt_path)
            return None
        try:
            content = prompt_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            logger.warning("Не удалось прочитать промпт %s: %s", prompt_path, exc)
            return None
        if not content:
            return None
        self.save_prompt(
            prompt_name=prompt_name,
            version=1,
            content=content,
            is_active=True,
            approved_by="system",
        )
        logger.info("Промпт %s импортирован в prompt_history как версия 1", prompt_name)
        return self.get_active_prompt(prompt_name)

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
