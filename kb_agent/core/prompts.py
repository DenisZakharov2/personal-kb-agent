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


def summarize_prompt_path(config: AppConfig) -> Path:
    """Путь к файлу промпта суммаризации."""
    return Path(config.prompts_dir) / "summarize_prompt.md"


def answer_prompt_path(config: AppConfig) -> Path:
    """Путь к файлу промпта ответов на вопросы."""
    return Path(config.prompts_dir) / "answer_prompt.md"


# Тип задачи для каждого промпта: определяет, для каких генераций он используется.
PROMPT_TYPES = {
    DEFAULT_PROMPT_NAME: "search",  # ответы на вопросы (kb ask / kb_search)
    SUMMARIZE_PROMPT_NAME: "summarize",  # суммаризация сырого материала в заметку
    ANSWER_PROMPT_NAME: "answer",  # ответы на вопросы (альтернативное имя)
}


def ensure_prompt_seeded(config: AppConfig, state: StateManager) -> dict | None:
    """Импортирует базовые версии промптов в prompt_history (однократно).

    Просеиваются ``agent_prompt``, ``summarize_prompt`` и ``answer_prompt`` из
    соответствующих файлов, чтобы у каждой области был свой активный промпт.
    Каждому промпту присваивается ``prompt_type`` (search/summarize/answer),
    чтобы активную версию можно было выбирать по задаче.
    Возвращает последнюю созданную запись или None, если активные версии уже есть.
    """
    from kb_agent.config import get_prompt_text

    seeded: dict | None = None
    # agent_prompt (search)
    base_text = get_prompt_text("agent_prompt.md", config)
    result = state.seed_prompt_from_text(
        DEFAULT_PROMPT_NAME,
        base_text or "",
        prompt_type=PROMPT_TYPES[DEFAULT_PROMPT_NAME],
    )
    if result:
        seeded = result
        logger.info(
            "Промпт %s (тип %s): активна версия %s",
            DEFAULT_PROMPT_NAME,
            PROMPT_TYPES[DEFAULT_PROMPT_NAME],
            result.get("version"),
        )
    # summarize_prompt
    base_text = get_prompt_text("summarize_prompt.md", config)
    result = state.seed_prompt_from_text(
        SUMMARIZE_PROMPT_NAME,
        base_text or "",
        prompt_type=PROMPT_TYPES[SUMMARIZE_PROMPT_NAME],
    )
    if result:
        seeded = result
        logger.info(
            "Промпт %s (тип %s): активна версия %s",
            SUMMARIZE_PROMPT_NAME,
            PROMPT_TYPES[SUMMARIZE_PROMPT_NAME],
            result.get("version"),
        )
    # answer_prompt
    base_text = get_prompt_text("answer_prompt.md", config)
    result = state.seed_prompt_from_text(
        ANSWER_PROMPT_NAME,
        base_text or "",
        prompt_type=PROMPT_TYPES[ANSWER_PROMPT_NAME],
    )
    if result:
        seeded = result
        logger.info(
            "Промпт %s (тип %s): активна версия %s",
            ANSWER_PROMPT_NAME,
            PROMPT_TYPES[ANSWER_PROMPT_NAME],
            result.get("version"),
        )
    return seeded


def get_active_system_prompt(
    state: StateManager, prompt_name: str = DEFAULT_PROMPT_NAME
) -> str | None:
    """Содержимое активной версии промпта ``prompt_name`` или None.

    По умолчанию возвращает ``agent_prompt`` (ответы на вопросы). Для суммаризации
    используйте ``prompt_name=SUMMARIZE_PROMPT_NAME``.
    """
    row = state.get_active_prompt(prompt_name)
    if not row:
        return None
    content = (row.get("content") or "").strip()
    return content or None
