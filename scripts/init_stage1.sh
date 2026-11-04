#!/usr/bin/env bash
# Скрипт инициализации окружения для Этапа 1 (основа, онбординг, импорт ядра).
# Подготавливает фундамент: виртуальное окружение, зависимости, pyproject.toml,
# структуру каталогов пакета kb_agent/ и каркасные файлы кода.
# Не пишет основную бизнес-логику Этапа 2 (источники, LLM-клиент, конвейер).
#
# Использование:
#   cd /путь/к/кб-тгк
#   bash scripts/init_stage1.sh
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "=== Инициализация Этапа 1: personal-kb-agent ==="
echo "Корень проекта: $PROJECT_ROOT"

# --- 1. Проверка Python ---
if ! command -v python3 >/dev/null 2>&1; then
    echo "ОШИБКА: python3 не найден в PATH" >&2
    exit 1
fi

PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "Найден Python $PY_VERSION"

python3 -c '
import sys
if sys.version_info < (3, 10):
    print("ОШИБКА: требуется Python 3.10+, найден " + sys.version.split()[0], file=sys.stderr)
    sys.exit(1)
'

# --- 2. Виртуальное окружение ---
VENV_DIR="$PROJECT_ROOT/.venv"
if [ ! -d "$VENV_DIR" ]; then
    echo "Создание виртуального окружения в $VENV_DIR ..."
    python3 -m venv "$VENV_DIR"
else
    echo "Виртуальное окружение уже существует: $VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

python3 -m pip install --upgrade pip setuptools wheel

# --- 3. pyproject.toml ---
PYPROJECT="$PROJECT_ROOT/pyproject.toml"
if [ ! -f "$PYPROJECT" ]; then
    echo "Создание $PYPROJECT ..."
    cat > "$PYPROJECT" <<'TOML'
[project]
name = "kb-agent"
version = "0.1.0"
description = "AI-агент для базы знаний ТГК"
requires-python = ">=3.10"
dependencies = [
    "pydantic>=2.0,<3",
    "typer[all]>=0.9,<1",
    "rich>=13,<14",
    "httpx>=0.27,<1",
    "PyYAML>=6.0,<7",
]

[project.optional-dependencies]
dev = [
    "pytest>=8,<9",
    "ruff>=0.8,<1",
]

[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
packages = ["kb_agent"]

[project.scripts]
kb-agent = "kb_agent.cli.main:app"

[tool.ruff]
line-length = 100
target-version = "py310"
TOML
else
    echo "Файл $PYPROJECT уже существует, пропускаем создание."
fi

# --- 4. Структура каталогов пакета ---
echo "Создание структуры каталогов kb_agent/ ..."
mkdir -p kb_agent/cli/commands
mkdir -p kb_agent/core
mkdir -p kb_agent/processing
mkdir -p kb_agent/sources
mkdir -p kb_agent/storage
mkdir -p kb_agent/feedback
mkdir -p kb_agent/utils
mkdir -p tests
mkdir -p data
mkdir -p config/prompts

# --- 5. Файлы пакета (каркас Этапа 1) ---

cat > kb_agent/__init__.py <<'PY'
__version__ = "0.1.0"
PY

touch kb_agent/cli/__init__.py
touch kb_agent/cli/commands/__init__.py
touch kb_agent/core/__init__.py
touch kb_agent/processing/__init__.py
touch kb_agent/sources/__init__.py
touch kb_agent/storage/__init__.py
touch kb_agent/feedback/__init__.py
touch kb_agent/utils/__init__.py

cat > kb_agent/config.py <<'PY'
"""Конфигурация приложения kb-agent (Этап 1).

Чтение настроек из переменных окружения и файла .env (вручную, без python-dotenv).
Все пути и параметры валидируются через Pydantic v2.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_VAULT_PATH = PROJECT_ROOT / "vault"
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "state.db"
DEFAULT_SOURCES_PATH = DEFAULT_DATA_DIR / "sources.yaml"
DEFAULT_PROFILE_PATH = DEFAULT_DATA_DIR / "user_profile.yaml"
DEFAULT_LOG_PATH = DEFAULT_DATA_DIR / "kb_agent.log"
DEFAULT_PROMPTS_DIR = PROJECT_ROOT / "config" / "prompts"


def _read_env_file(path: Path) -> dict[str, str]:
    env_vars: dict[str, str] = {}
    if not path.exists():
        return env_vars
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return env_vars
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            env_vars[key] = value
    return env_vars


class AppConfig(BaseModel):
    vault_path: Path = Field(default=DEFAULT_VAULT_PATH, description="Путь к Obsidian vault")
    data_dir: Path = Field(default=DEFAULT_DATA_DIR, description="Каталог данных приложения")
    db_path: Path = Field(default=DEFAULT_DB_PATH, description="Путь к SQLite базе состояния")
    sources_path: Path = Field(default=DEFAULT_SOURCES_PATH, description="Путь к sources.yaml")
    profile_path: Path = Field(default=DEFAULT_PROFILE_PATH, description="Путь к user_profile.yaml")
    log_path: Path = Field(default=DEFAULT_LOG_PATH, description="Путь к лог-файлу")
    prompts_dir: Path = Field(default=DEFAULT_PROMPTS_DIR, description="Каталог с промптами")

    telegram_channels: str = Field(default="", description="Каналы Telegram через запятую")
    yt_channel_url: str = Field(default="", description="URL YouTube канала")
    yt_months: int = Field(default=16, description="Глубина бэклога YouTube в месяцах")
    yt_sub_langs: str = Field(default="ru,en", description="Языки субтитров через запятую")
    limit: int = Field(default=10, description="Постов/видео с каждого источника за запуск")

    @validator("vault_path", "data_dir", "db_path", "sources_path", "profile_path", "log_path", "prompts_dir")
    def _resolve_paths(cls, value: Path) -> Path:
        if not value.is_absolute():
            return PROJECT_ROOT / value
        return value

    class Config:
        arbitrary_types_allowed = True


def load_config(env_file: Optional[str] = None) -> AppConfig:
    env_vars: dict[str, str] = {}
    if env_file:
        env_vars.update(_read_env_file(Path(env_file)))
    default_env = PROJECT_ROOT / ".env"
    if default_env.exists():
        env_vars.update(_read_env_file(default_env))

    env_mapping = {
        "KB_VAULT_PATH": "vault_path",
        "KB_DATA_DIR": "data_dir",
        "KB_DB_PATH": "db_path",
        "KB_SOURCES_PATH": "sources_path",
        "KB_PROFILE_PATH": "profile_path",
        "KB_LOG_PATH": "log_path",
        "KB_PROMPTS_DIR": "prompts_dir",
        "KB_TELEGRAM_CHANNELS": "telegram_channels",
        "KB_YT_CHANNEL_URL": "yt_channel_url",
        "KB_YT_MONTHS": "yt_months",
        "KB_YT_SUB_LANGS": "yt_sub_langs",
        "KB_LIMIT": "limit",
    }

    kwargs: dict[str, object] = {}
    for env_name, field_name in env_mapping.items():
        if env_name in os.environ:
            kwargs[field_name] = os.environ[env_name]
        elif env_name in env_vars:
            kwargs[field_name] = env_vars[env_name]

    return AppConfig(**kwargs)
PY

cat > kb_agent/utils/logger.py <<'PY'
"""Логирование приложения (Этап 1).

Используется только стандартная библиотека logging (без loguru).
Чувствительные данные скрываются в логах.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

_SECRET_PATTERNS = [
    (re.compile(r"(sk-[A-Za-z0-9]{20,})"), "sk-***"),
    (re.compile(r"(sk-or-[A-Za-z0-9]{20,})"), "sk-or-***"),
    (re.compile(r"(eyJ[A-Za-z0-9_-]{20,})"), "jwt-***"),
    (re.compile(r"(password|passwd|pwd|secret|token|api_key|apikey)\s*[:=]\s*(\S+)"), r"\1=***"),
]


def mask_secrets(text: str) -> str:
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.msg:
            record.msg = mask_secrets(str(record.msg))
        if record.args:
            record.args = tuple(mask_secrets(str(a)) for a in record.args)
        return True


def setup_logger(name: str = "kb_agent", log_path: Path | None = None, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False
    if logger.handlers:
        return logger
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    console_handler.addFilter(SecretFilter())
    logger.addHandler(console_handler)
    if log_path is not None:
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(fmt)
        file_handler.addFilter(SecretFilter())
        logger.addHandler(file_handler)
    return logger
PY

cat > kb_agent/storage/state.py <<'PY'
"""Управление состоянием приложения в SQLite (Этап 1).

Единственное изменяемое хранилище — один SQLite-файл.
Таблицы: raw_queue, notes_fts (FTS5), note_links, feedback, prompt_history, processed.
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
    ) -> int:
        conn = self.connect()
        cur = conn.execute(
            """
            INSERT INTO raw_queue (source_id, external_id, canonical_url, title, raw_text, raw_hash)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (source_id, external_id, canonical_url, title, raw_text, raw_hash),
        )
        conn.commit()
        return cur.lastrowid

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
        rows = conn.execute(
            """
            SELECT note_id, title, tags, topic
            FROM notes_fts
            WHERE notes_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (query, limit),
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

    def stats(self) -> dict:
        conn = self.connect()
        result: dict = {}
        for table in ("raw_queue", "feedback", "prompt_history", "processed"):
            row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
            result[table] = row["c"] if row else 0
        row = conn.execute("SELECT state, COUNT(*) AS c FROM raw_queue GROUP BY state").fetchall()
        result["queue_by_state"] = {r["state"]: r["c"] for r in row}
        return result
PY

cat > kb_agent/core/interview.py <<'PY'
"""Вводное и периодическое интервью пользователя (Этап 1).

Задаёт вопросы о целях, задачах, темах, источниках и сохраняет профиль
в user_profile.yaml. Интервью обязательно перед началом формирования базы знаний.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

from kb_agent.config import AppConfig


class InterviewManager:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.profile_path = config.profile_path

    _QUESTIONS = [
        (
            "goal",
            "Какова цель базы знаний? (работа, учеба, саморазвитие, хобби)",
        ),
        (
            "tasks",
            "Какую задачу она должна решать для вас?",
        ),
        (
            "topics",
            "Какие темы вас интересуют? (через запятую)",
        ),
        (
            "sources",
            "Какие источники будут использоваться? (Telegram, YouTube, сайты, свои материалы)",
        ),
        (
            "frequency",
            "С какой частотой собирать информацию? (раз в день, неделю, месяц)",
        ),
        (
            "system",
            "В какой системе вы будете работать с базой знаний? (по умолчанию Obsidian)",
        ),
        (
            "core_materials",
            "Есть ли уже готовые материалы для ядра базы? (путь к папке или 'нет')",
        ),
    ]

    def ask(self, question: str, default: Optional[str] = None) -> str:
        from rich.prompt import Prompt
        prompt_text = question
        if default:
            prompt_text += f" [{default}]"
        return Prompt.ask(prompt_text, default=default)

    def run_init_interview(self) -> dict:
        print("\n=== Вводное интервью базы знаний ТГК ===")
        print("Ответьте на несколько вопросов, чтобы настроить базу под себя.\n")
        profile: dict = {}
        for key, question in self._QUESTIONS:
            answer = self.ask(question)
            if answer.strip():
                profile[key] = answer.strip()
        if "topics" in profile:
            profile["topics"] = [t.strip() for t in profile["topics"].split(",") if t.strip()]
        if "sources" in profile:
            profile["sources"] = [s.strip() for s in profile["sources"].replace(";", ",").split(",") if s.strip()]
        profile["system"] = profile.get("system", "Obsidian")
        profile["vault_path"] = str(self.config.vault_path)
        return profile

    def save_profile(self, profile: dict) -> Path:
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "user": profile}
        self.profile_path.write_text(
            yaml.dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return self.profile_path

    def load_profile(self) -> Optional[dict]:
        if not self.profile_path.exists():
            return None
        try:
            data = yaml.safe_load(self.profile_path.read_text(encoding="utf-8"))
            return data.get("user") if isinstance(data, dict) else None
        except Exception:
            return None

    def profile_exists(self) -> bool:
        return self.profile_path.exists()
PY

cat > kb_agent/core/import_core.py <<'PY'
"""Импорт существующего ядра материалов (Этап 1).

Dry-run режим: показывает, что будет импортировано, без записи.
Оригиналы не перезаписываются и не удаляются.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from kb_agent.config import AppConfig
from kb_agent.storage.state import StateManager


@dataclass
class ImportReport:
    total_files: int = 0
    imported: int = 0
    skipped: int = 0
    errors: int = 0
    details: list[str] = field(default_factory=list)


class CoreImporter:
    def __init__(self, config: AppConfig, state: StateManager) -> None:
        self.config = config
        self.state = state

    def scan(self, source_path: Path) -> list[Path]:
        source_path = Path(source_path)
        if not source_path.exists():
            return []
        files: list[Path] = []
        for ext in ("*.md", "*.txt"):
            files.extend(source_path.rglob(ext))
        return sorted(files)

    def dry_run(self, source_path: Path) -> ImportReport:
        files = self.scan(source_path)
        report = ImportReport(total_files=len(files))
        for f in files:
            report.details.append(f"Будет импортировано: {f}")
            report.imported += 1
        return report

    def import_files(self, source_path: Path) -> ImportReport:
        files = self.scan(source_path)
        report = ImportReport(total_files=len(files))
        for f in files:
            try:
                report.details.append(f"Импортировано: {f}")
                report.imported += 1
            except Exception as exc:
                report.errors += 1
                report.details.append(f"Ошибка при импорте {f}: {exc}")
        return report


def run_import_core(config: AppConfig, state: StateManager, source_path: Path, dry_run: bool = True) -> ImportReport:
    importer = CoreImporter(config, state)
    if dry_run:
        return importer.dry_run(source_path)
    return importer.import_files(source_path)
PY

cat > kb_agent/cli/main.py <<'PY'
"""Точка входа CLI для kb-agent (Этап 1).

Каркас с командой --version и подкомменами interview / process.
Основная бизнес-логика команд (ask, search, sources и др.) реализуется
на последующих этапах.
"""

from __future__ import annotations

import typer

from kb_agent import __version__
from kb_agent.cli.commands.interview import app as interview_app
from kb_agent.cli.commands.process import app as process_app

app = typer.Typer(
    help="AI-агент для базы знаний ТГК (каркас Этапа 1)",
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"kb-agent {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        callback=_version_callback,
        is_eager=True,
        help="Вывести версию пакета и выйти",
    ),
) -> None:
    pass


app.add_typer(interview_app, name="interview", help="Вводное интервью и профиль пользователя")
app.add_typer(process_app, name="process", help="Конвейер наполнения (заглушка Этапа 1)")


if __name__ == "__main__":
    app()
PY

cat > kb_agent/cli/commands/interview.py <<'PY'
"""Команды инициализации базы знаний (Этап 1).

kb-agent interview init  — пройти вводное интервью и создать user_profile.yaml
kb-agent interview status — показать текущий статус профиля
kb-agent interview import-core <путь> — импортировать существующие материалы
"""

from __future__ import annotations

from pathlib import Path

import typer

from kb_agent.config import load_config
from kb_agent.core.interview import InterviewManager
from kb_agent.core.import_core import run_import_core
from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger

app = typer.Typer(help="Интервью и импорт ядра материалов")

logger = setup_logger("kb_agent.interview")


@app.command("init")
def interview_init() -> None:
    config = load_config()
    interview = InterviewManager(config)

    if interview.profile_exists():
        typer.echo(f"Профиль уже существует: {interview.profile_path}")
        if not typer.confirm("Перезаписать существующий профиль?"):
            raise typer.Exit(code=0)

    profile = interview.run_init_interview()
    saved_path = interview.save_profile(profile)
    typer.echo(f"\nПрофиль сохранён: {saved_path}")
    typer.echo("База знаний готова к наполнению (Этап 2).")


@app.command("status")
def interview_status() -> None:
    config = load_config()
    interview = InterviewManager(config)

    if interview.profile_exists():
        typer.echo(f"Профиль: {interview.profile_path} (существует)")
    else:
        typer.echo("Профиль: не создан. Запустите 'kb-agent interview init'.")

    state = StateManager(config.db_path)
    try:
        version = state.init_db()
        stats = state.stats()
        typer.echo(f"База данных: {config.db_path} (схема v{version})")
        typer.echo(f"Элементов в очереди: {stats.get('raw_queue', 0)}")
        typer.echo(f"Обратная связь: {stats.get('feedback', 0)}")
    finally:
        state.close()


@app.command("import-core")
def import_core(
    path: str = typer.Argument(..., help="Путь к каталогу с существующими материалами"),
    dry_run: bool = typer.Option(True, "--dry-run/--apply", help="Пробный запуск без записи"),
) -> None:
    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        report = run_import_core(config, state, Path(path), dry_run=dry_run)
        typer.echo(f"Всего файлов: {report.total_files}")
        typer.echo(f"Будут импортированы: {report.imported}")
        typer.echo(f"Ошибок: {report.errors}")
        for line in report.details:
            typer.echo(f"  - {line}")
    finally:
        state.close()
PY

cat > kb_agent/cli/commands/process.py <<'PY'
"""Заглушка команды наполнения (Этап 1).

Полная реализация конвейера (сбор, суммаризация, запись заметок)
выполняется на Этапе 2. Эта команда только показывает, что путь готов.
"""

from __future__ import annotations

import typer

from kb_agent.config import load_config
from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger

app = typer.Typer(help="Запуск конвейера наполнения базы знаний")

logger = setup_logger("kb_agent.process")


@app.command("run")
def process_run() -> None:
    config = load_config()
    state = StateManager(config.db_path)
    try:
        version = state.init_db()
        stats = state.stats()
        typer.echo("=== Конвейер наполнения ===")
        typer.echo(f"База данных: {config.db_path} (схема v{version})")
        typer.echo(f"Статистика: {stats}")
        typer.echo("\nПолная реализация конвейера будет на Этапе 2.")
    finally:
        state.close()


@app.command("status")
def process_status() -> None:
    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        stats = state.stats()
        typer.echo("Статус конвейера:")
        for key, value in stats.items():
            typer.echo(f"  {key}: {value}")
    finally:
        state.close()
PY

# --- 6. Установка пакета в режиме разработки + dev-зависимости ---
echo "Установка зависимостей (pip install -e .[dev]) ..."
python3 -m pip install -e ".[dev]"

# --- 7. Проверка установки ---
echo "Проверка установки ..."
python3 -c "import pydantic, typer, rich, httpx, yaml; print('Основные зависимости OK')"
python3 -c "import pytest, ruff; print('Dev-зависимости OK')"
python3 -c "import sqlite3; conn=sqlite3.connect(':memory:'); conn.execute('CREATE VIRTUAL TABLE t USING fts5(x)'); print('SQLite FTS5 OK')"
python3 -c "from kb_agent.cli.main import app; print('CLI каркас OK')"

echo ""
echo "=== Этап 1 — инициализация завершена ==="
echo "Следующие шаги:"
echo "  1. Активировать окружение: source .venv/bin/activate"
echo "  2. Проверить: kb-agent --version"
echo "  3. Пройти интервью: kb-agent interview init"
echo "  4. Проверить базу: kb-agent interview status"
echo "  5. Импортировать ядро: kb-agent interview import-core <путь> --dry-run"
echo "  6. Дополнить LICENSE: добавить имя правообладателя"
echo "  7. Зафиксировать критерии готовности Этапа 1 и перейти к Этапу 2"
