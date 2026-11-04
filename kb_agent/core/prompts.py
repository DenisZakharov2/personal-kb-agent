"""Версионируемые системные промпты (Этап 5).

Активная версия промпта хранится в SQLite (таблица prompt_history) и при первом
запуске импортируется из config/prompts/agent_prompt.md. Благодаря этому работают
`kb-agent self-improve rollback` и MCP-инструмент `kb_revert_prompt`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from kb_agent.config import AppConfig
from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.core.prompts")

# Имя промпта в prompt_history
DEFAULT_PROMPT_NAME = "agent_prompt"


def agent_prompt_path(config: AppConfig) -> Path:
    """Путь к редактируемому файлу системного промпта."""
    return Path(config.prompts_dir) / "agent_prompt.md"


def ensure_prompt_seeded(config: AppConfig, state: StateManager) -> Optional[dict]:
    """Импортирует базовую версию промпта в prompt_history (однократно).

    Возвращает созданную запись или None, если активная версия уже есть.
    """
    seeded = state.seed_prompt_from_file(DEFAULT_PROMPT_NAME, agent_prompt_path(config))
    if seeded:
        logger.info("Промпт %s: активна версия %s", DEFAULT_PROMPT_NAME, seeded.get("version"))
    return seeded


def get_active_system_prompt(state: StateManager) -> Optional[str]:
    """Содержимое активной версии системного промпта или None."""
    row = state.get_active_prompt(DEFAULT_PROMPT_NAME)
    if not row:
        return None
    content = (row.get("content") or "").strip()
    return content or None
