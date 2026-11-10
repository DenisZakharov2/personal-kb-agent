"""Единая утилита для санизации YAML frontmatter (Этап 2/5).

Экранирует YAML-значения, которые ``safe_load`` не примет вне кавычек.
PyYAML не принимает:
- скаляр, начинающийся с ``@`` (например ``источник: @ragnarokzone``),
- строку с ``: `` внутри значения (например ``title: Что-то: с двоеточием``),
что ломает mapping. Оборачиваем такие значения в одинарные кавычки,
если они ещё не экранированы.
"""

from __future__ import annotations


def sanitize_frontmatter(fm_text: str) -> str:
    """Экранирует YAML-значения, которые ``safe_load`` не примет вне кавычек.

    Возвращает исправленный текст frontmatter, пригодный для
    ``yaml.safe_load``.
    """
    fixed_lines: list[str] = []
    for line in fm_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            fixed_lines.append(line)
            continue
        colon_pos = stripped.find(": ")
        if colon_pos == -1:
            fixed_lines.append(line)
            continue
        key = stripped[:colon_pos]
        value = stripped[colon_pos + 2 :]
        # Не в кавычках и содержит спецсимволы YAML — экранируем.
        if (
            value
            and value[0] not in ('"', "'")
            and (
                value.startswith(("@", "- ", "#", "*", "[", "{"))
                or ": " in value
                or value.lower() in ("true", "false", "null", "~")
            )
        ):
            indent = line[: len(line) - len(line.lstrip())]
            value_escaped = "'" + value.replace("'", "''") + "'"
            line = f"{indent}{key}: {value_escaped}"
        fixed_lines.append(line)
    return "\n".join(fixed_lines)
