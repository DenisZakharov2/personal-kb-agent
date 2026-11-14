"""Единый антиспам-фильтр для kb-agent (Этап 5).

Консолидирует списки спам-ключевых слов, которые ранее дублировались
в ``kb_agent/sources/telegram.py`` (SPAM_KEYWORDS) и
``kb_agent/core/relevance.py`` (SPAM_PATTERNS).

Единый источник истины упрощает обслуживание: любое добавление или
удаление ключевого слова вносится в одном месте и автоматически
применяется и к сбору из Telegram, и к Relevance Gate конвейера.

Проверка идёт **по границам слова**: «купи» больше не срабатывает на
«купить квартиру», а «реклама» — на «рекламный». Для слов с частыми
словоформами используется основа (розыгрыш\\_*, скидк\\_*, промокод\\_*).

Функция :func:`detect_spam` возвращает причину срабатывания — она
сохраняется в ``skipped_reason`` очереди, поэтому ошибочно
отфильтрованный пост можно вернуть через ``kb-agent retry <id>``.
"""

from __future__ import annotations

import re

# --- Многословные фразы: проверяются как подстроки ---
SPAM_PHRASES: tuple[str, ...] = (
    "подпишись на канал",
    "подписаться на",
    "розыгрыш приза",
    "бесплатный розыгрыш",
)

# --- Однословные маркеры: совпадают только как целое слово ---
# «купи» не должно ловить «купить квартиру», «реклама» — «рекламный».
SPAM_EXACT_MARKERS: tuple[str, ...] = (
    "купи",
    "реклама",
    "giveaway",
    "вложения",
    "заработай",
    "инвестируй",
)

# --- Однословные маркеры по основе: ловят словоформы ---
# розыгрыш / розыгрыши, скидка / скидки / скидку, промокод / промокоды.
SPAM_STEM_MARKERS: tuple[str, ...] = (
    "розыгрыш",
    "скидк",
    "промокод",
)

# --- Список спам-ключевых слов и фраз (обратная совместимость) ---
# Используется как публичная константа; сама проверка — в detect_spam().
SPAM_KEYWORDS: tuple[str, ...] = (
    "розыгрыш",
    "giveaway",
    "подпишись на канал",
    "подписаться на",
    "реклама",
    "вложения",
    "заработай",
    "инвестируй",
    "купи",
    "промокод",
    "скидка",
    "розыгрыш приза",
    "бесплатный розыгрыш",
)

# --- compiled regex с границами слов для Relevance Gate ---
# Поддерживает словоформы (розыгрыш\w*, промокод\w*) и многословные фразы.
SPAM_PATTERNS = re.compile(
    r"\b(?:розыгрыш\w*|giveaway|промокод\w*|бесплатный\s+розыгрыш|подпишись\s+на\s+канал)\b",
    re.IGNORECASE,
)

# Точное совпадение слова (без суффиксов): (?<!\w)маркер(?!\w)
_EXACT_MARKER_RES = {
    marker: re.compile(rf"(?<!\w){re.escape(marker)}(?!\w)", re.IGNORECASE | re.UNICODE)
    for marker in SPAM_EXACT_MARKERS
}

# Совпадение по основе с необязательным окончанием: (?<!\w)основа\w*
_STEM_MARKER_RES = {
    marker: re.compile(rf"(?<!\w){re.escape(marker)}\w*", re.IGNORECASE | re.UNICODE)
    for marker in SPAM_STEM_MARKERS
}


def detect_spam(text: str) -> str | None:
    """Проверяет текст на спам и возвращает причину срабатывания.

    Возвращает ``None``, если текст не похож на спам. Иначе — строку вида
    ``"спам: маркер «скидка»"`` (её удобно положить в ``skipped_reason``).
    """
    if not text:
        return None
    lower = text.lower()
    for phrase in SPAM_PHRASES:
        if phrase in lower:
            return f"спам: фраза «{phrase}»"
    for marker, pattern in _EXACT_MARKER_RES.items():
        if pattern.search(text):
            return f"спам: маркер «{marker}»"
    for marker, pattern in _STEM_MARKER_RES.items():
        if pattern.search(text):
            return f"спам: маркер «{marker}»"
    return None


def is_spam(text: str) -> bool:
    """Проверяет текст на спам по ключевым словам.

    Регистронезависимая проверка с учётом границ слов (см. :func:`detect_spam`).
    """
    return detect_spam(text) is not None
