"""Конфигурация приложения kb-agent (Этапы 1-2).

Чтение настроек из переменных окружения и файла .env (вручную, без python-dotenv).
Все пути и параметры валидируются через Pydantic v2.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, validator

# Корень проекта: две уровни выше от этого файла (kb_agent/config.py -> kb_agent/ -> корень)
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Пути по умолчанию
DEFAULT_VAULT_PATH = Path("/home/anony/Documents/Личное/База знаний")
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "state.db"
DEFAULT_SOURCES_PATH = DEFAULT_DATA_DIR / "sources.yaml"
DEFAULT_PROFILE_PATH = DEFAULT_DATA_DIR / "user_profile.yaml"
DEFAULT_LOG_PATH = DEFAULT_DATA_DIR / "kb_agent.log"
DEFAULT_PROMPTS_DIR = PROJECT_ROOT / "config" / "prompts"


def _read_env_file(path: Path) -> dict[str, str]:
    """Читает .env файл вручную (без библиотеки python-dotenv).

    Возвращает словарь переменных. Не бросает исключений, если файла нет.
    """
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

    # Параметры сбора (из .env / переменных окружения)
    telegram_channels: str = Field(
        default="rubiconzone,paragonzone,ragnarokzone",
        description="Каналы Telegram через запятую",
    )
    youtube_channels: str = Field(
        default="https://www.youtube.com/@amorev,https://www.youtube.com/@rubiconzone,https://www.youtube.com/@paragonzone,https://www.youtube.com/@javaswag,https://www.youtube.com/@AIAutomation-n8n,https://www.youtube.com/@yefomov_AI,https://www.youtube.com/@ProdAdvice,https://www.youtube.com/@VolchenkoAI,https://www.youtube.com/@nagorra,https://www.youtube.com/@vladimir_kkkkkk",
        description="Каналы YouTube через запятую",
    )
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
    """Загружает конфигурацию.

    Приоритет: переменные окружения > файл .env > значения по умолчанию.
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

    return AppConfig(**kwargs)
