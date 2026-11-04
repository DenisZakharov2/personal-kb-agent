"""Версионируемые системные промпты (Этап 5).

Активные версии промптов хранятся в SQLite (таблица prompt_history) и при первом
запуске импортируются из kb_agent/resources/prompts/agent_prompt.md.
Благодаря этому работают `kb-agent self-improve rollback` и MCP-инструмент
`kb_revert_prompt`.

Разделены две области ответственности:
  * summarize_prompt — суммаризация сырого материала в заметку (конвейер сбора);
  * answer_prompt    — ответ на вопрос пользователя по найденным заметкам (поиск).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from kb_agent.config import AppConfig
from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.core.prompts")

# Имена промптов в prompt_history
DEFAULT_PROMPT_NAME = "agent_prompt"
SUMMARIZE_PROMPT_NAME = "summarize_prompt"
ANSWER_PROMPT_NAME = "answer_prompt"

# Все промпты, которые должны быть просеяны при первом запуске.
_ALL_PROMPT_NAMES = (DEFAULT_PROMPT_NAME, SUMMARIZE_PROMPT_NAME, ANSWER_PROMPT_NAME)


def agent_prompt_path(config: AppConfig) -> Path:
    """Путь к редактируемому файлу базового промпта."""
    return Path(config.prompts_dir) / "agent_prompt.md"


# Тип задачи для каждого промпта: определяет, для каких генераций он используется.
PROMPT_TYPES = {
    DEFAULT_PROMPT_NAME: "search",      # ответы на вопросы (kb ask / kb_search)
    SUMMARIZE_PROMPT_NAME: "summarize", # суммаризация сырого материала в заметку
    ANSWER_PROMPT_NAME: "search",       # ответы на вопросы (альтернативное имя)
}


def ensure_prompt_seeded(config: AppConfig, state: StateManager) -> Optional[dict]:
    """Импортирует базовые версии промптов в prompt_history (однократно).

    Просеиваются ``agent_prompt``, ``summarize_prompt`` и ``answer_prompt`` из
    одного исходного файла, чтобы у каждой области был свой активный промпт.
    Каждому промпту присваивается ``prompt_type`` (search/summarize/insights),
    чтобы активную версию можно было выбирать по задаче.
    Возвращает последнюю созданную запись или None, если активные версии уже есть.
    """
    # Базовое содержимое берём из упакованного ресурса, с fallback на каталог
    # config/prompts при разработке из исходников.
    from kb_agent.config import get_prompt_path

    base_path = get_prompt_path(config, "agent_prompt.md") or agent_prompt_path(config)
    seeded: Optional[dict] = None
    for prompt_name in _ALL_PROMPT_NAMES:
        result = state.seed_prompt_from_file(
            prompt_name, base_path, prompt_type=PROMPT_TYPES.get(prompt_name, "search")
        )
        if result:
            seeded = result
            logger.info(
                "Промпт %s (тип %s): активна версия %s",
                prompt_name,
                PROMPT_TYPES.get(prompt_name, "search"),
                result.get("version"),
            )
    return seeded


def get_active_system_prompt(state: StateManager, prompt_name: str = DEFAULT_PROMPT_NAME) -> Optional[str]:
    """Содержимое активной версии промпта ``prompt_name`` или None.

    По умолчанию возвращает ``agent_prompt`` (ответы на вопросы). Для суммаризации
    используйте ``prompt_name=SUMMARIZE_PROMPT_NAME``.
    """
    row = state.get_active_prompt(prompt_name)
    if not row:
        return None
    content = (row.get("content") or "").strip()
    return content or None
