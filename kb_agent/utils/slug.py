"""Единый helper для формирования slug (безопасные имена файлов/папок)."""

from __future__ import annotations

import re


def slugify(text: str) -> str:
    """Преобразует текст в безопасное имя папки/файла.

    Правила: приведение к нижнему регистру, удаление посторонних символов,
    замена пробелов/подчёркиваний на одно `_`, обрезка до 80 символов.
    Пустой результат заменяется на «Общее».
    """
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_]+", "_", text)
    return text[:80] or "Общее"
