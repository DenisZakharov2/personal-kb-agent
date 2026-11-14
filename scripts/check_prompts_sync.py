#!/usr/bin/env python3
"""Проверка синхронности промптов: config/prompts ↔ kb_agent/resources/prompts.

Промпты хранятся в двух местах: в репозитории (``config/prompts`` — их
редактирует пользователь) и в пакете (``kb_agent/resources/prompts`` — они
попадают в wheel). Расхождение означает, что установленная версия работает
с другими промптами, чем те, что лежат в репозитории.

Скрипт сравнивает содержимое файлов и завершается с кодом 1 при расхождении.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = REPO_ROOT / "config" / "prompts"
RESOURCE_DIR = REPO_ROOT / "kb_agent" / "resources" / "prompts"


def main() -> int:
    if not SOURCE_DIR.is_dir():
        print(f"✗ Не найден каталог {SOURCE_DIR}")
        return 1
    if not RESOURCE_DIR.is_dir():
        print(f"✗ Не найден каталог {RESOURCE_DIR}")
        return 1

    source_files = {p.name: p for p in sorted(SOURCE_DIR.glob("*.md"))}
    resource_files = {p.name: p for p in sorted(RESOURCE_DIR.glob("*.md"))}

    problems: list[str] = []

    for name in sorted(set(source_files) | set(resource_files)):
        in_source = name in source_files
        in_resource = name in resource_files
        if in_source and not in_resource:
            problems.append(f"{name}: есть в config/prompts, но нет в kb_agent/resources/prompts")
            continue
        if in_resource and not in_source:
            problems.append(f"{name}: есть в kb_agent/resources/prompts, но нет в config/prompts")
            continue
        source_text = source_files[name].read_text(encoding="utf-8")
        resource_text = resource_files[name].read_text(encoding="utf-8")
        if source_text != resource_text:
            problems.append(
                f"{name}: содержимое config/prompts и kb_agent/resources/prompts различается"
            )

    if problems:
        print("✗ Промпты рассинхронизированы:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "\n  Скопируйте config/prompts/*.md в kb_agent/resources/prompts/ "
            "(или наоборот) и закоммитьте изменения.",
            file=sys.stderr,
        )
        return 1

    print(f"✓ Промпты синхронны ({len(source_files)} файлов)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
