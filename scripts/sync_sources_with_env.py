#!/usr/bin/env python3
"""Синхронизирует список источников базы знаний с конфигурацией окружения.

Зачем: каноническое хранилище источников — SQLite, и накопленные записи
переживают смену конфигурации. Если канал убран из ``KB_TELEGRAM_CHANNELS`` /
``KB_YOUTUBE_CHANNELS`` (или там остался плейсхолдер вида ``@example_channel``
из ``.env.example``), ежедневный прогон в CI будет падать на несуществующем
источнике.

Скрипт отключает (а не удаляет) источники, которых нет в текущей конфигурации:
- отключение обратимо — вернуть источник можно командой
  ``kb-agent sources enable <source_id>``;
- источники из конфигурации остаются включёнными.

Использование:
    python scripts/sync_sources_with_env.py [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kb_agent.config import load_config
from kb_agent.sources.registry import normalize_source
from kb_agent.storage.state import StateManager


def configured_source_ids() -> set[str]:
    """Возвращает source_id источников, перечисленных в окружении."""
    ids: set[str] = set()

    telegram = os.environ.get("KB_TELEGRAM_CHANNELS", "")
    for channel in (part.strip() for part in telegram.split(",")):
        if not channel:
            continue
        try:
            source_id, _channel, _type = normalize_source("telegram", channel)
        except ValueError:
            continue
        ids.add(source_id)

    youtube = os.environ.get("KB_YOUTUBE_CHANNELS", "")
    for channel in (part.strip() for part in youtube.split(",")):
        if not channel:
            continue
        try:
            source_id, _channel, _type = normalize_source("youtube", channel)
        except ValueError:
            continue
        ids.add(source_id)

    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description="Синхронизация источников с окружением")
    parser.add_argument("--dry-run", action="store_true", help="Только показать, что изменится")
    args = parser.parse_args()

    config = load_config()
    state = StateManager(config.db_file)
    try:
        state.init_db()
        configured = configured_source_ids()

        if not configured:
            print(
                "KB_TELEGRAM_CHANNELS / KB_YOUTUBE_CHANNELS не заданы (или секреты пусты). "
                "Синхронизация пропущена, чтобы случайно не отключить все источники."
            )
            return 0

        rows = state.list_sources()
        if not rows:
            print("Источников в базе нет — нечего синхронизировать.")
            return 0

        to_disable = [
            row for row in rows if row.get("enabled") and row.get("source_id") not in configured
        ]
        if not to_disable:
            print(f"Источники в актуальном состоянии ({len(configured)} в конфигурации).")
            return 0

        for row in to_disable:
            source_id = row["source_id"]
            if args.dry_run:
                print(f"Будет отключён: {source_id}")
                continue
            state.set_source_enabled(source_id, False)
            print(f"Отключён (нет в конфигурации): {source_id}")

        if not args.dry_run:
            print(f"Готово: отключено {len(to_disable)}, активно в конфигурации {len(configured)}.")
        return 0
    finally:
        state.close()


if __name__ == "__main__":
    raise SystemExit(main())
