"""Запись заметок в Obsidian vault (Этап 2).

Формирует .md файл с YAML frontmatter и секциями суммаризации.
Авторитетная копия заметки — файл в vault.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import re

from kb_agent.config import AppConfig
from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.processing.kb_writer")

FRONTMATTER_SCHEMA_VERSION = 1


def _slugify(text: str) -> str:
    """Преобразует заголовок в безопасное имя файла."""
    text = text.strip().lower()
    # Заменяем пробелы и спецсимволы на _
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_]+", "_", text)
    return text[:80]


def _clean_title_for_filename(title: str) -> str:
    """Безопасное имя файла из заголовка заметки."""
    return _slugify(title) or "note"


class KBWriter:
    """Пишет суммаризированные заметки в Obsidian vault."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.vault_path = config.vault_path
        self.vault_path.mkdir(parents=True, exist_ok=True)

    def write_note(
        self,
        *,
        note_id: str,
        source_id: str,
        source_type: str,
        external_id: str,
        canonical_url: str,
        published_at: Optional[str],
        received_at: str,
        title: str,
        tags: str,
        topic: str,
        content: str,
        previous_note_id: Optional[str] = None,
    ) -> Path:
        """Создаёт .md файл с YAML frontmatter и возвращает путь к нему."""
        # Папка по типу источника (Телеграмм / YouTube)
        source_folder = "Телеграмм" if source_type == "telegram" else "YouTube"
        folder = self.vault_path / source_folder
        folder.mkdir(parents=True, exist_ok=True)

        file_name = f"{received_at[:10]}_{_clean_title_for_filename(title)}.md"
        file_path = folder / file_name

        # Если файл с таким именем уже существует, добавляем суффикс
        if file_path.exists():
            file_name = f"{received_at[:10]}_{_clean_title_for_filename(title)}_{note_id[:8]}.md"
            file_path = folder / file_name

        frontmatter = {
            "source": source_type,
            "source_id": source_id,
            "external_id": external_id,
            "url": canonical_url,
            "date_saved": received_at[:10],
            "published_at": published_at,
            "received_at": received_at,
            "note_id": note_id,
            "previous_note_id": previous_note_id,
            "frontmatter_schema_version": FRONTMATTER_SCHEMA_VERSION,
            "title": title,
            "tags": tags.split(",") if tags else [],
            "topic": topic,
        }

        lines: list[str] = []
        lines.append("---")
        for key, value in frontmatter.items():
            if value is None:
                lines.append(f"{key}:")
            elif isinstance(value, list):
                lines.append(f"{key}: {value}")
            else:
                lines.append(f"{key}: {value}")
        lines.append("---")
        lines.append("")
        lines.append(f"# {title}")
        lines.append("")
        lines.append(content)
        lines.append("")

        file_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Запись заметки: %s", file_path)
        return file_path
