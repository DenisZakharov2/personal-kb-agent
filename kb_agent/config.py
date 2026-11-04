"""Конфигурация приложения kb-agent (Этапы 1-2).

Чтение настроек из переменных окружения и файла .env (вручную, без python-dotenv).
Все пути и параметры валидируются через Pydantic v2.

Упакованные ресурсы (промпты, примеры конфигурации) читаются через
importlib.resources из пакета kb_agent.resources. Пользовательские данные
(vault, БД, профиль, источники) размещаются в data_dir (по умолчанию
PROJECT_ROOT/data при установке из исходников, или KB_DATA_DIR при установке
через wheel).
"""

from __future__ import annotations

import os
from importlib import resources as importlib_resources
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Корень проекта (для установки из исходников). При установке через wheel
# PROJECT_ROOT указывает в site-packages, поэтому пользовательские данные
# должны браться из KB_DATA_DIR / data_dir, а не отсюда.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Пакет с ресурсами (промпты, примеры конфигов), доставляемый с wheel
RESOURCES_PACKAGE = "kb_agent.resources"

# Пути по умолчанию
DEFAULT_VAULT_PATH = Path("~/База знаний").expanduser()
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "state.db"
DEFAULT_SOURCES_PATH = DEFAULT_DATA_DIR / "sources.yaml"
DEFAULT_PROFILE_PATH = DEFAULT_DATA_DIR / "user_profile.yaml"
DEFAULT_LOG_PATH = DEFAULT_DATA_DIR / "kb_agent.log"
DEFAULT_PROMPTS_DIR = PROJECT_ROOT / "config" / "prompts"  # fallback для исходников


def _read_env_file(path: Path) -> dict[str, str]:
    """Читает .env файл вручную (без библиотеки python-dotenv)."""
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


def _resource_path(package: str, resource_name: str) -> Optional[Path]:
    """Возвращает путь к упакованному ресурсу через importlib.resources."""
    try:
        with importlib_resources.files(package).joinpath(resource_name) as ref:
            return Path(ref)
    except (FileNotFoundError, ModuleNotFoundError, ImportError, TypeError):
        return None


class AppConfig(BaseModel):
    """Основная конфигурация приложения."""

    vault_path: Path = Field(default=DEFAULT_VAULT_PATH, description="Путь к Obsidian vault")
    data_dir: Path = Field(default=DEFAULT_DATA_DIR, description="Каталог данных приложения")
    db_path: Path = Field(default=DEFAULT_DB_PATH, description="Путь к SQLite базе состояния")
    sources_path: Path = Field(default=DEFAULT_SOURCES_PATH, description="Путь к sources.yaml")
    profile_path: Path = Field(default=DEFAULT_PROFILE_PATH, description="Путь к user_profile.yaml")
    log_path: Path = Field(default=DEFAULT_LOG_PATH, description="Путь к лог-файлу")
    prompts_dir: Path = Field(default=DEFAULT_PROMPTS_DIR, description="Каталог с промптами")

    # LLM
    llm_provider: str = Field(default="openai", description="Провайдер: openai, kimi, anthropic, ollama, openrouter, custom")
    openai_api_key: Optional[str] = Field(default=None, description="API-ключ LLM-провайдера")
    openai_base_url: str = Field(default="https://api.openai.com/v1", description="Базовый URL API провайдера")
    openai_model: str = Field(default="gpt-4o-mini", description="Модель LLM")
    openai_temperature: float = Field(default=0.3, description="Температура модели")
    openai_max_tokens: int = Field(default=2000, description="Максимум токенов в ответе")
    llm_timeout: float = Field(default=60.0, description="Таймаут запроса к LLM, секунд")
    llm_max_retries: int = Field(
        default=2,
        description="Число повторов запроса при 429/5xx и сетевых ошибках",
    )

    # Параметры сбора (из .env / переменных окружения)
    telegram_channels: str = Field(
        default="",
        description="Каналы Telegram через запятую (используются при создании sources.yaml)",
    )
    youtube_channels: str = Field(
        default="",
        description="Каналы YouTube через запятую (используются при создании sources.yaml)",
    )
    yt_months: int = Field(default=16, description="Глубина бэклога YouTube в месяцах")
    yt_sub_langs: str = Field(default="ru,en", description="Языки субтитров через запятую")
    limit: int = Field(default=10, description="Постов/видео с каждого источника за запуск")

    @field_validator(
        "vault_path",
        "data_dir",
        "db_path",
        "sources_path",
        "profile_path",
        "log_path",
        "prompts_dir",
        mode="after",
    )
    @classmethod
    def _resolve_paths(cls, value: Path) -> Path:
        if not value.is_absolute():
            return PROJECT_ROOT / value
        return value

    model_config = ConfigDict(arbitrary_types_allowed=True)


def load_config(env_file: Optional[str] = None) -> AppConfig:
    """Загружает конфигурацию.

    Приоритет: переменные окружения > файл .env > значения по умолчанию.

    Если задан KB_DATA_DIR, все производные пути (db, sources, profile, log)
    вычисляются относительно него, если не заданы явно.
    """
    env_vars: dict[str, str] = {}

    # 1. Сначала читаем .env файл (если указан)
    if env_file:
        env_vars.update(_read_env_file(Path(env_file)))

    # 2. Также пробуем стандартный .env в корне проекта
    default_env = PROJECT_ROOT / ".env"
    if default_env.exists():
        env_vars.update(_read_env_file(default_env))

    # 3. Переменные окружения имеют наивысший приоритет
    env_mapping = {
        "KB_VAULT_PATH": "vault_path",
        "KB_DATA_DIR": "data_dir",
        "KB_DB_PATH": "db_path",
        "KB_SOURCES_PATH": "sources_path",
        "KB_PROFILE_PATH": "profile_path",
        "KB_LOG_PATH": "log_path",
        "KB_PROMPTS_DIR": "prompts_dir",
        "KB_LLM_PROVIDER": "llm_provider",
        "KB_OPENAI_API_KEY": "openai_api_key",
        "KB_OPENAI_BASE_URL": "openai_base_url",
        "KB_OPENAI_MODEL": "openai_model",
        "KB_OPENAI_TEMPERATURE": "openai_temperature",
        "KB_OPENAI_MAX_TOKENS": "openai_max_tokens",
        "KB_LLM_TIMEOUT": "llm_timeout",
        "KB_LLM_MAX_RETRIES": "llm_max_retries",
        "KB_TELEGRAM_CHANNELS": "telegram_channels",
        "KB_YOUTUBE_CHANNELS": "youtube_channels",
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

    config = AppConfig(**kwargs)

    # Если задан KB_DATA_DIR, вычисляем производные пути относительно него,
    # если они не заданы явно через переменные окружения.
    if "KB_DATA_DIR" in os.environ or "KB_DATA_DIR" in env_vars:
        data_dir = config.data_dir
        if "KB_DB_PATH" not in os.environ and "KB_DB_PATH" not in env_vars:
            config.db_path = data_dir / "state.db"
        if "KB_SOURCES_PATH" not in os.environ and "KB_SOURCES_PATH" not in env_vars:
            config.sources_path = data_dir / "sources.yaml"
        if "KB_PROFILE_PATH" not in os.environ and "KB_PROFILE_PATH" not in env_vars:
            config.profile_path = data_dir / "user_profile.yaml"
        if "KB_LOG_PATH" not in os.environ and "KB_LOG_PATH" not in env_vars:
            config.log_path = data_dir / "kb_agent.log"

    return config


def get_prompt_path(prompt_name: str) -> Optional[Path]:
    """Возвращает путь к упакованному промпту через importlib.resources.

    Сначала ищет в kb_agent.resources/prompts, затем в config/prompts
    (для разработки из исходников).
    """
    path = _resource_path(RESOURCES_PACKAGE, f"prompts/{prompt_name}")
    if path is not None:
        return path
    # Fallback для разработки из исходников
    fallback = PROJECT_ROOT / "config" / "prompts" / prompt_name
    if fallback.exists():
        return fallback
    return None