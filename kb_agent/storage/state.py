"""Управление состоянием приложения в SQLite (Этап 1).

Каноническое изменяемое хранилище — один SQLite-файл; также меняются файлы vault и sources.yaml.
Таблицы: raw_queue, notes (канонические метаданные заметок), notes_fts (FTS5-проекция),
note_links, feedback, prompt_history, processed, pending_changes (Этап 4), sources (Этап 4).
"""

from __future__ import annotations

import hashlib
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
            status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending','confirmed','applied','rejected','failed','expired')),
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
    (
        12,
        """
        CREATE TABLE IF NOT EXISTS notes (
            note_id TEXT PRIMARY KEY,
            title TEXT,
            topic TEXT,
            tags TEXT,
            source_id TEXT,
            external_id TEXT,
            canonical_url TEXT,
            file_path TEXT,
            content_hash TEXT,
            revision INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """,
    ),
    (
        13,
        """
        -- Пересоздаём pending_changes: добавляем колонки ревизии/TTL/отказа/аудита
        -- и расширяем CHECK-констрейнт статуса (значение 'expired' не входит в
        -- старую проверку, поэтому простого ALTER TABLE ADD COLUMN недостаточно).
        DROP TABLE IF EXISTS pending_changes_old;
        ALTER TABLE pending_changes RENAME TO pending_changes_old;
        CREATE TABLE pending_changes (
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
            result TEXT,
            revision INTEGER DEFAULT 0,
            expires_at TEXT,
            reject_reason TEXT,
            content_hash TEXT
        );
        INSERT INTO pending_changes (id, change_id, action, entity_type, entity_id, parameters, status,
                                     created_at, confirmed_at, applied_at, confirmed_by, result)
        SELECT id, change_id, action, entity_type, entity_id, parameters, status,
               created_at, confirmed_at, applied_at, confirmed_by, result
        FROM pending_changes_old;
        DROP TABLE pending_changes_old;
        """,
    ),
    (
        14,
        """
        CREATE TABLE IF NOT EXISTS prompt_proposals (
            proposal_id TEXT PRIMARY KEY,
            target TEXT NOT NULL CHECK (target IN ('prompt','config','source')),
            target_name TEXT NOT NULL,
            current_version INTEGER,
            proposed_version INTEGER,
            diff TEXT,
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'proposed'
                CHECK (status IN ('proposed','confirmed','applied','rejected','failed')),
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            confirmed_at TEXT,
            applied_at TEXT,
            confirmed_by TEXT,
            test_passed INTEGER NOT NULL DEFAULT 0,
            eval_set_id TEXT
        );
        """,
    ),
    (
        15,
        """
        ALTER TABLE pending_changes ADD COLUMN diff TEXT;
        """,
    ),
    (
        16,
        """
        ALTER TABLE pending_changes ADD COLUMN created_by TEXT;
        """,
    ),
    (
        17,
        """
        ALTER TABLE notes ADD COLUMN is_private INTEGER NOT NULL DEFAULT 0;
        """,
    ),
    (
        18,
        """
        ALTER TABLE sources ADD COLUMN is_private INTEGER NOT NULL DEFAULT 0;
        """,
    ),
    (
        19,
        """
        CREATE TABLE IF NOT EXISTS note_raw_content (
            note_id TEXT PRIMARY KEY,
            raw_content TEXT NOT NULL,
            source_id TEXT,
            external_id TEXT,
            canonical_url TEXT,
            raw_hash TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """,
    ),
    (
        20,
        """
        ALTER TABLE notes ADD COLUMN is_archived INTEGER NOT NULL DEFAULT 0;
        """,
    ),
    (
        21,
        """
        ALTER TABLE prompt_history ADD COLUMN prompt_type TEXT NOT NULL DEFAULT 'search';
        """,
    ),
    (
        22,
        """
        CREATE TABLE IF NOT EXISTS generation_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            note_id TEXT,
            operation TEXT NOT NULL CHECK (operation IN ('summarize','answer','insights')),
            model TEXT NOT NULL,
            prompt_name TEXT,
            prompt_version INTEGER,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            latency_ms REAL NOT NULL DEFAULT 0,
            cost_usd REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """,
    ),
    (
        23,
        """
        ALTER TABLE raw_queue ADD COLUMN note_id TEXT;
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
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA busy_timeout = 10000")
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

    def get_queue_item(self, queue_id: int) -> Optional[dict]:
        """Возвращает запись raw_queue по id."""
        conn = self.connect()
        row = conn.execute(
            "SELECT * FROM raw_queue WHERE id = ?", (queue_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_queue_item_by_external(
        self, source_id: str, external_id: str
    ) -> Optional[dict]:
        """Возвращает запись raw_queue по паре (source_id, external_id)."""
        conn = self.connect()
        row = conn.execute(
            """
            SELECT * FROM raw_queue
            WHERE source_id = ? AND external_id = ?
            ORDER BY id DESC LIMIT 1
            """,
            (source_id, external_id),
        ).fetchone()
        return dict(row) if row else None

    def set_queue_item_note_id(self, queue_id: int, note_id: str) -> None:
        """Сохраняет note_id в записи raw_queue (для идемпотентного повтора).

        Вызывается сразу после успешной записи файла в vault, чтобы при
        повторной обработке того же элемента использовать тот же note_id и не
        создавать дубли файлов.
        """
        conn = self.connect()
        conn.execute(
            "UPDATE raw_queue SET note_id = ? WHERE id = ?",
            (note_id, queue_id),
        )
        conn.commit()

    def get_queue_items_without_note_id(self) -> list[dict]:
        """Возвращает элементы raw_queue в состоянии processed/failed, у которых
        не сохранён note_id (Possible orphans после частичного сбоя)."""
        conn = self.connect()
        rows = conn.execute(
            """
            SELECT * FROM raw_queue
            WHERE state IN ('processed', 'failed')
              AND note_id IS NULL
            ORDER BY received_at DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]

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

    def mark_failed(
        self,
        queue_id: int,
        error: str,
        next_retry_at: Optional[str] = None,
        *,
        backoff: bool = True,
    ) -> None:
        """Помечает элемент очереди как ``failed`` с планом повторного запуска.

        Если ``next_retry_at`` не задан и ``backoff=True``, вычисляется время
        следующей попытки с экспоненциальной задержкой на основе текущего
        ``attempts``: ``min(2^attempts минут, 24 часа)``. Это позволяет
        ``claim_next`` автоматически подхватить элемент после истечения задержки
        (P1-06: failed-элементы не застревают навсегда).
        """
        conn = self.connect()
        if next_retry_at is None and backoff:
            row = conn.execute(
                "SELECT attempts FROM raw_queue WHERE id = ?", (queue_id,)
            ).fetchone()
            current_attempts = int(row["attempts"] or 0) if row else 0
            # Экспоненциальный backoff: 1, 2, 4, 8, ... минут, максимум 24 часа.
            delay_minutes = min(2 ** current_attempts, 60 * 24)
            next_retry_at = (
                datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)
            ).isoformat()
        conn.execute(
            """
            UPDATE raw_queue
            SET state = 'failed', last_error = ?, next_retry_at = ?,
                lease_expires_at = NULL, attempts = attempts + 1
            WHERE id = ?
            """,
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

    def index_note(
        self,
        note_id: str,
        title: str,
        tags: str,
        topic: str,
        content: str,
        *,
        source_id: Optional[str] = None,
        external_id: Optional[str] = None,
        canonical_url: Optional[str] = None,
        file_path: Optional[str] = None,
        content_hash: Optional[str] = None,
        revision: int = 1,
        is_private: int = 0,
        is_archived: int = 0,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        """Индексирует заметку в обе таблицы (notes + notes_fts) в одной транзакции.

        ``notes`` — каноническое хранилище метаданных, ``notes_fts`` — rebuildable
        проекция для полнотекстового поиска. При повторной индексации того же
        note_id запись в notes обновляется (revision увеличивается).
        """
        c = conn or self.connect()
        own = conn is None
        try:
            if own:
                c.execute("BEGIN IMMEDIATE")
            # Проверяем существующую запись: если content_hash не изменился,
            # не перезаписываем и не увеличиваем revision (идемпотентность).
            existing = c.execute(
                "SELECT content_hash, revision FROM notes WHERE note_id = ?",
                (note_id,),
            ).fetchone()
            if (
                existing is not None
                and existing["content_hash"] is not None
                and existing["content_hash"] == content_hash
            ):
                # Содержимое не изменилось — обновляем только FTS-проекцию,
                # revision оставляем без изменений.
                c.execute("DELETE FROM notes_fts WHERE note_id = ?", (note_id,))
                c.execute(
                    """
                    INSERT INTO notes_fts (note_id, title, tags, topic, content)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (note_id, title, tags, topic, content),
                )
                if own:
                    c.commit()
                return
            c.execute(
                """
                INSERT INTO notes (note_id, title, tags, topic, source_id, external_id, canonical_url,
                                   file_path, content_hash, revision, is_private, is_archived, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                ON CONFLICT(note_id) DO UPDATE SET
                    title = excluded.title,
                    tags = excluded.tags,
                    topic = excluded.topic,
                    source_id = excluded.source_id,
                    external_id = excluded.external_id,
                    canonical_url = excluded.canonical_url,
                    file_path = excluded.file_path,
                    content_hash = excluded.content_hash,
                    revision = notes.revision + 1,
                    is_private = excluded.is_private,
                    is_archived = excluded.is_archived,
                    updated_at = datetime('now')
                """,
                (
                    note_id,
                    title,
                    tags,
                    topic,
                    source_id,
                    external_id,
                    canonical_url,
                    file_path,
                    content_hash,
                    revision,
                    is_private,
                    is_archived,
                ),
            )
            c.execute("DELETE FROM notes_fts WHERE note_id = ?", (note_id,))
            c.execute(
                """
                INSERT INTO notes_fts (note_id, title, tags, topic, content)
                VALUES (?, ?, ?, ?, ?)
                """,
                (note_id, title, tags, topic, content),
            )
            if own:
                c.commit()
        except Exception:
            if own:
                c.rollback()
            raise

    def remove_from_index(self, note_id: str) -> None:
        """Удаляет заметку из FTS-проекции (запись в notes сохраняется)."""
        conn = self.connect()
        conn.execute("DELETE FROM notes_fts WHERE note_id = ?", (note_id,))
        conn.commit()

    def save_raw_content(
        self,
        note_id: str,
        raw_content: str,
        *,
        source_id: Optional[str] = None,
        external_id: Optional[str] = None,
        canonical_url: Optional[str] = None,
        raw_hash: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        """Сохраняет/обновляет исходное содержимое заметки, привязанное к note_id.

        Хранится отдельно от суммаризации, чтобы заметку можно было
        пересобрать другой моделью или промптом без повторного сбора источника.
        """
        c = conn or self.connect()
        own = conn is None
        try:
            if own:
                c.execute("BEGIN IMMEDIATE")
            c.execute(
                """
                INSERT INTO note_raw_content (note_id, raw_content, source_id, external_id,
                                              canonical_url, raw_hash, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                ON CONFLICT(note_id) DO UPDATE SET
                    raw_content = excluded.raw_content,
                    source_id = excluded.source_id,
                    external_id = excluded.external_id,
                    canonical_url = excluded.canonical_url,
                    raw_hash = excluded.raw_hash,
                    updated_at = datetime('now')
                """,
                (note_id, raw_content, source_id, external_id, canonical_url, raw_hash),
            )
            if own:
                c.commit()
        except Exception:
            if own:
                c.rollback()
            raise

    def get_raw_content(self, note_id: str) -> Optional[dict]:
        """Возвращает исходное содержимое заметки по note_id или None."""
        conn = self.connect()
        row = conn.execute(
            "SELECT * FROM note_raw_content WHERE note_id = ?", (note_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_note_by_external(self, source_id: str, external_id: str) -> Optional[dict]:
        """Находит существующую заметку по паре (source_id, external_id).

        Используется для идемпотентного обновления: при повторном сборе
        того же источника обновляем уже имеющуюся заметку (revision + 1),
        а не создаём новую.
        """
        conn = self.connect()
        row = conn.execute(
            """
            SELECT * FROM notes
            WHERE source_id = ? AND external_id = ?
            ORDER BY revision DESC
            LIMIT 1
            """,
            (source_id, external_id),
        ).fetchone()
        return dict(row) if row else None

    def list_queue(
        self, state: Optional[str] = None, limit: int = 100
    ) -> list[dict]:
        """Возвращает элементы raw_queue, опционально отфильтрованные по состоянию.

        Например, state='skipped' — просмотр пропущенных материалов, которые
        можно вернуть в ``new`` через ``kb-agent retry <id>``.
        """
        conn = self.connect()
        if state:
            rows = conn.execute(
                """
                SELECT * FROM raw_queue
                WHERE state = ?
                ORDER BY received_at DESC
                LIMIT ?
                """,
                (state, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM raw_queue
                ORDER BY received_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_note_archived(self, note_id: str, archived: bool = True) -> bool:
        """Устанавливает/снимает флаг is_archived у заметки.

        Архивные заметки исключаются из обычного поиска (state.search с
        include_archived=False) и из каталога, но остаются в notes и FTS
        для возможности восстановления.
        """
        conn = self.connect()
        cur = conn.execute(
            "UPDATE notes SET is_archived = ?, updated_at = datetime('now') WHERE note_id = ?",
            (1 if archived else 0, note_id),
        )
        conn.commit()
        return cur.rowcount > 0

    def merge_notes(self, keep_note_id: str, drop_note_id: str) -> dict:
        """Объединяет две заметки: ``drop_note_id`` архивируется, а ``keep_note_id`` остаётся.

        Выполняется в одной транзакции ``BEGIN IMMEDIATE`` (защита от параллельного
        запуска). Никакая заметка не удаляется физически из ``notes`` — дубль только
        помечается ``is_archived = 1``, чтобы сохранить историю и возможность отката.

        Переносит все ссылки из ``note_links``, указывавшие на дубль, на основную
        заметку (самоссылки и дубликаты удаляются). При необходимости копирует
        ``raw_content`` дубля в основную заметку и удаляет дубль из ``notes_fts``.

        Возвращает отчёт с ключами:
        kept_note_id, dropped_note_id, links_moved, raw_content_moved, fts_entries_removed.
        """
        conn = self.connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            keep_row = conn.execute(
                "SELECT * FROM notes WHERE note_id = ?", (keep_note_id,)
            ).fetchone()
            drop_row = conn.execute(
                "SELECT * FROM notes WHERE note_id = ?", (drop_note_id,)
            ).fetchone()
            if keep_row is None or drop_row is None:
                conn.rollback()
                raise ValueError(
                    f"Заметка не найдена: keep={keep_note_id}, drop={drop_note_id}"
                )

            # a) notes — дубль помечается архивным, основная остаётся без изменений.
            conn.execute(
                """
                UPDATE notes
                SET is_archived = 1, updated_at = datetime('now')
                WHERE note_id = ?
                """,
                (drop_note_id,),
            )

            # b) note_links — все связи дубля переназначаются на основную заметку.
            drop_links = conn.execute(
                """
                SELECT source_note_id, target_note_id, link_type
                FROM note_links
                WHERE source_note_id = ? OR target_note_id = ?
                """,
                (drop_note_id, drop_note_id),
            ).fetchall()

            links_moved = 0
            for row in drop_links:
                src = (
                    keep_note_id
                    if row["source_note_id"] == drop_note_id
                    else row["source_note_id"]
                )
                tgt = (
                    keep_note_id
                    if row["target_note_id"] == drop_note_id
                    else row["target_note_id"]
                )
                link_type = row["link_type"]

                # Удаляем исходную строку (она ссылается на дубль).
                conn.execute(
                    """
                    DELETE FROM note_links
                    WHERE source_note_id = ? AND target_note_id = ? AND link_type = ?
                    """,
                    (row["source_note_id"], row["target_note_id"], link_type),
                )
                if src == tgt:
                    # После переназначения получилась само-ссылка — не восстанавливаем.
                    links_moved += 1
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO note_links
                        (source_note_id, target_note_id, link_type)
                    VALUES (?, ?, ?)
                    """,
                    (src, tgt, link_type),
                )
                links_moved += 1

            # c) note_raw_content — если у дубля есть контент, а у основной нет,
            # копируем его в основную; запись дубля удаляется.
            raw_content_moved = False
            keep_raw = conn.execute(
                "SELECT 1 FROM note_raw_content WHERE note_id = ?", (keep_note_id,)
            ).fetchone()
            drop_raw = conn.execute(
                "SELECT * FROM note_raw_content WHERE note_id = ?", (drop_note_id,)
            ).fetchone()
            if drop_raw is not None and keep_raw is None:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO note_raw_content
                        (note_id, raw_content, source_id, external_id,
                         canonical_url, raw_hash, created_at, updated_at)
                    SELECT
                        ?, raw_content, source_id, external_id,
                        canonical_url, raw_hash, created_at, datetime('now')
                    FROM note_raw_content
                    WHERE note_id = ?
                    """,
                    (keep_note_id, drop_note_id),
                )
                raw_content_moved = True
            conn.execute(
                "DELETE FROM note_raw_content WHERE note_id = ?", (drop_note_id,)
            )

            # d) notes_fts — удаляем дубль из поисковой проекции.
            drop_fts = conn.execute(
                "SELECT 1 FROM notes_fts WHERE note_id = ?", (drop_note_id,)
            ).fetchone()
            fts_entries_removed = 0
            if drop_fts:
                conn.execute(
                    "DELETE FROM notes_fts WHERE note_id = ?", (drop_note_id,)
                )
                fts_entries_removed = 1

            conn.commit()
        except Exception:
            conn.rollback()
            raise

        return {
            "kept_note_id": keep_note_id,
            "dropped_note_id": drop_note_id,
            "links_moved": links_moved,
            "raw_content_moved": raw_content_moved,
            "fts_entries_removed": fts_entries_removed,
        }

    def save_generation_log(
        self,
        note_id: Optional[str],
        operation: str,
        model: str,
        *,
        prompt_name: Optional[str] = None,
        prompt_version: Optional[int] = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: float = 0.0,
        cost_usd: float = 0.0,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        """Записывает метаданные одной генерации (модель, промпт, токены,
        задержка, стоимость) в generation_log."""
        allowed = ("summarize", "answer", "insights")
        if operation not in allowed:
            raise ValueError(f"Недопустимая операция: {operation}")
        c = conn or self.connect()
        own = conn is None
        try:
            if own:
                c.execute("BEGIN IMMEDIATE")
            cur = c.execute(
                """
                INSERT INTO generation_log
                    (note_id, operation, model, prompt_name, prompt_version,
                     prompt_tokens, completion_tokens, latency_ms, cost_usd, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    note_id,
                    operation,
                    model,
                    prompt_name,
                    prompt_version,
                    int(prompt_tokens or 0),
                    int(completion_tokens or 0),
                    float(latency_ms or 0.0),
                    float(cost_usd or 0.0),
                ),
            )
            if own:
                c.commit()
            return cur.lastrowid
        except Exception:
            if own:
                c.rollback()
            raise

    def rebuild_fts(self) -> int:
        """Перестраивает notes_fts целиком из канонической таблицы notes.

        Используется при восстановлении после частичного сбоя или при
        миграции: все старые записи FTS удаляются, затем вставляются актуальные
        из notes. Возвращает количество проиндексированных заметок.
        """
        conn = self.connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM notes_fts")
            cur = conn.execute(
                """
                INSERT INTO notes_fts (note_id, title, tags, topic, content)
                SELECT note_id, title, tags, topic, content_hash
                FROM notes
                """
            )
            conn.commit()
            return cur.rowcount
        except Exception:
            conn.rollback()
            raise

    def search(
        self,
        query: str,
        limit: int = 10,
        *,
        source_id: Optional[str] = None,
        topic: Optional[str] = None,
        tags: Optional[list[str]] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        include_archived: bool = False,
    ) -> list[dict]:
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

        # Дополнительные фильтры накладываются на каноническую таблицу notes
        # (проекция rebuildable, метаданные provenance хранятся там).
        conditions: list[str] = []
        params: list[object] = [fts_query]
        if source_id is not None:
            conditions.append("n.source_id = ?")
            params.append(source_id)
        if topic is not None:
            conditions.append("n.topic = ?")
            params.append(topic)
        if tags:
            for tag in tags:
                if not tag.strip():
                    continue
                # tags хранятся как "тэг1,тэг2"; ищем целевой тег с запятыми по краям,
                # добавляя фиктивные запятые вокруг значения столбца.
                conditions.append("',' || COALESCE(n.tags, '') || ',' LIKE ?")
                params.append(f"%,{tag.strip()},%")
        if date_from is not None:
            conditions.append("date(n.created_at) >= date(?)")
            params.append(date_from)
        if date_to is not None:
            conditions.append("date(n.created_at) <= date(?)")
            params.append(date_to)
        if not include_archived:
            conditions.append("(n.is_archived = 0 OR n.is_archived IS NULL)")
        where_clause = (
            "WHERE notes_fts MATCH ?"
            if not conditions
            else "WHERE notes_fts MATCH ? AND " + " AND ".join(conditions)
        )
        try:
            rows = conn.execute(
                f"""
                SELECT f.note_id, f.title, f.tags, f.topic, f.content, f.rank,
                       n.canonical_url, n.source_id, n.external_id, n.file_path,
                       n.content_hash, n.revision, n.is_private, n.is_archived
                FROM notes_fts f
                LEFT JOIN notes n ON n.note_id = f.note_id
                {where_clause}
                ORDER BY rank
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            # Некорректный с точки зрения FTS5 запрос не должен ронять CLI/MCP.
            logger.warning("FTS-запрос отклонён (%s): %s", fts_query, exc)
            return []
        return [dict(row) for row in rows]

    def get_note(self, note_id: str) -> Optional[dict]:
        """Возвращает заметку по ID.

        Метаданные берутся из канонической таблицы ``notes``, содержимое —
        из FTS-проекции ``notes_fts`` (единственного хранилища контента).
        """
        conn = self.connect()
        row = conn.execute(
            """
            SELECT n.note_id, n.title, n.tags, n.topic, n.source_id, n.external_id,
                   n.canonical_url, n.file_path, n.content_hash, n.revision,
                   n.created_at, n.updated_at, n.is_private, n.is_archived, f.content
            FROM notes n
            LEFT JOIN notes_fts f ON f.note_id = n.note_id
            WHERE n.note_id = ?
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
        *,
        prompt_type: str = "search",
        is_active: bool = False,
        approved_by: Optional[str] = None,
        reverted_from: Optional[int] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> int:
        c = conn or self.connect()
        own = conn is None
        cur = c.execute(
            """
            INSERT INTO prompt_history (prompt_name, version, content, prompt_type, is_active,
                                        approved_by, reverted_from)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                prompt_name,
                version,
                content,
                prompt_type,
                int(is_active),
                approved_by,
                reverted_from,
            ),
        )
        if own:
            c.commit()
        return cur.lastrowid

    def get_active_prompt(
        self, prompt_name: str, prompt_type: Optional[str] = None
    ) -> Optional[dict]:
        conn = self.connect()
        if prompt_type is not None:
            row = conn.execute(
                """
                SELECT * FROM prompt_history
                WHERE prompt_name = ? AND is_active = 1 AND prompt_type = ?
                ORDER BY version DESC LIMIT 1
                """,
                (prompt_name, prompt_type),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT * FROM prompt_history
                WHERE prompt_name = ? AND is_active = 1
                ORDER BY version DESC LIMIT 1
                """,
                (prompt_name,),
            ).fetchone()
        return dict(row) if row else None

    def seed_prompt_from_file(
        self, prompt_name: str, prompt_path: Path, prompt_type: str = "search"
    ) -> Optional[dict]:
        """Заводит версию 1 промпта из файла, если активной версии ещё нет.

        Нужен, чтобы `prompt_history` не оставалась пустой: без этого
        `self-improve rollback` и MCP `kb_revert_prompt` нечего откатывать.
        """
        if self.get_active_prompt(prompt_name, prompt_type=prompt_type):
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
            prompt_type=prompt_type,
            is_active=True,
            approved_by="system",
        )
        logger.info("Промпт %s импортирован в prompt_history как версия 1", prompt_name)
        return self.get_active_prompt(prompt_name, prompt_type=prompt_type)

    def seed_prompt_from_text(
        self,
        prompt_name: str,
        content: str,
        prompt_type: str = "search",
    ) -> Optional[dict]:
        """Заводит версию 1 промпта из строки, если активной версии ещё нет.

        Аналог ``seed_prompt_from_file``, но принимает содержимое промпта
        напрямую (для чтения из упакованных ресурсов через importlib.resources).
        """
        if self.get_active_prompt(prompt_name, prompt_type=prompt_type):
            return None
        content = (content or "").strip()
        if not content:
            return None
        self.save_prompt(
            prompt_name=prompt_name,
            version=1,
            content=content,
            prompt_type=prompt_type,
            is_active=True,
            approved_by="system",
        )
        logger.info(
            "Промпт %s (тип %s) импортирован в prompt_history как версия 1 из текста",
            prompt_name,
            prompt_type,
        )
        return self.get_active_prompt(prompt_name, prompt_type=prompt_type)

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
        conn: Optional[sqlite3.Connection] = None,
    ) -> Optional[int]:
        """Добавляет новый источник. Возвращает ID записи или None, если уже существует."""
        c = conn or self.connect()
        own = conn is None
        try:
            cur = c.execute(
                """
                INSERT INTO sources (source_id, source_type, channel, enabled)
                VALUES (?, ?, ?, ?)
                """,
                (source_id, source_type, channel, int(enabled)),
            )
            if own:
                c.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            if own:
                c.rollback()
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

    def set_source_enabled(self, source_id: str, enabled: bool, conn: Optional[sqlite3.Connection] = None) -> bool:
        c = conn or self.connect()
        own = conn is None
        cur = c.execute(
            """
            UPDATE sources
            SET enabled = ?, updated_at = datetime('now')
            WHERE source_id = ?
            """,
            (int(enabled), source_id),
        )
        if own:
            c.commit()
        return cur.rowcount > 0

    def remove_source(self, source_id: str, conn: Optional[sqlite3.Connection] = None) -> bool:
        c = conn or self.connect()
        own = conn is None
        cur = c.execute("DELETE FROM sources WHERE source_id = ?", (source_id,))
        if own:
            c.commit()
        return cur.rowcount > 0

    # ---- Предложения самоулучшения (Этап 5) ----

    def save_proposal(
        self,
        proposal_id: str,
        target: str,
        target_name: str,
        current_version: Optional[int],
        proposed_version: Optional[int],
        diff: str,
        reason: str,
        eval_set_id: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        """Сохраняет предложение улучшения в таблицу prompt_proposals."""
        c = conn or self.connect()
        own = conn is None
        try:
            c.execute(
                """
                INSERT INTO prompt_proposals
                    (proposal_id, target, target_name, current_version, proposed_version,
                     diff, reason, status, eval_set_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'proposed', ?)
                """,
                (
                    proposal_id,
                    target,
                    target_name,
                    current_version,
                    proposed_version,
                    diff,
                    reason,
                    eval_set_id,
                ),
            )
            if own:
                c.commit()
        except Exception:
            if own:
                c.rollback()
            raise

    def get_proposal(self, proposal_id: str) -> Optional[dict]:
        """Возвращает предложение по ID или None."""
        conn = self.connect()
        row = conn.execute(
            "SELECT * FROM prompt_proposals WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_proposals(self, status: str = "proposed") -> list[dict]:
        """Возвращает список предложений со статусом ``status``."""
        allowed = ("proposed", "confirmed", "applied", "rejected", "failed")
        if status not in allowed:
            raise ValueError(f"Недопустимый статус: {status}")
        conn = self.connect()
        rows = conn.execute(
            """
            SELECT * FROM prompt_proposals
            WHERE status = ?
            ORDER BY created_at DESC
            """,
            (status,),
        ).fetchall()
        return [dict(row) for row in rows]

    def update_proposal(
        self,
        proposal_id: str,
        conn: Optional[sqlite3.Connection] = None,
        **fields: object,
    ) -> bool:
        """Обновляет поля предложения (status, test_passed, confirmed_at и т.п.)."""
        if not fields:
            return False
        c = conn or self.connect()
        own = conn is None
        try:
            set_clause = ", ".join(f"{k} = ?" for k in fields)
            values = list(fields.values()) + [proposal_id]
            cur = c.execute(
                f"UPDATE prompt_proposals SET {set_clause} WHERE proposal_id = ?",
                values,
            )
            if own:
                c.commit()
            return cur.rowcount > 0
        except Exception:
            if own:
                c.rollback()
            raise

    def activate_prompt_version(
        self,
        prompt_name: str,
        version: int,
        confirmed_by: str = "user",
        conn: Optional[sqlite3.Connection] = None,
    ) -> bool:
        """Атомарно активирует версию промпта: снимает is_active со старых и
        ставит её на указанную в одной транзакции.

        Возвращает False, если целевая версия не найдена.
        """
        c = conn or self.connect()
        own = conn is None
        try:
            c.execute("BEGIN IMMEDIATE")
            target = c.execute(
                """
                SELECT 1 FROM prompt_history
                WHERE prompt_name = ? AND version = ?
                """,
                (prompt_name, version),
            ).fetchone()
            if target is None:
                if own:
                    c.rollback()
                return False
            c.execute(
                "UPDATE prompt_history SET is_active = 0 WHERE prompt_name = ?",
                (prompt_name,),
            )
            c.execute(
                """
                UPDATE prompt_history
                SET is_active = 1
                WHERE prompt_name = ? AND version = ?
                """,
                (prompt_name, version),
            )
            if own:
                c.commit()
            logger.info(
                "Промпт %s: версия %s активирована (подтвердил %s)",
                prompt_name, version, confirmed_by,
            )
            return True
        except Exception:
            if own:
                c.rollback()
            raise

    def stats(self) -> dict:
        conn = self.connect()
        result: dict = {}
        for table in (
            "raw_queue",
            "notes",
            "note_raw_content",
            "feedback",
            "prompt_history",
            "generation_log",
            "processed",
            "sources",
            "pending_changes",
        ):
            row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
            result[table] = row["c"] if row else 0
        row = conn.execute("SELECT state, COUNT(*) AS c FROM raw_queue GROUP BY state").fetchall()
        result["queue_by_state"] = {r["state"]: r["c"] for r in row}
        return result
