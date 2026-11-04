"""Менеджер хранилища Obsidian vault (Этап 2/5).

Безопасная запись заметок .md с YAML frontmatter в тематические папки
(по темам из профиля пользователя, а не по источнику).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import yaml

from kb_agent.config import AppConfig
from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.storage.vault")


def _slugify(text: str) -> str:
    """Преобразует текст в безопасное имя папки/файла."""
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_]+", "_", text)
    return text[:80] or "Общее"


class VaultManager:
    """Работа с файловой системой Obsidian vault."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.vault_path = Path(config.vault_path)
        self.vault_path.mkdir(parents=True, exist_ok=True)

    def _topic_folder(self, topic: str) -> Path:
        """Папка по тематике (из профиля или 'Общее')."""
        folder_name = _slugify(topic)
        folder = self.vault_path / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        return folder

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
        """Создаёт .md файл с YAML frontmatter и возвращает путь к нему.

        Запись атомарная: через временный файл с subsequent replace.
        """
        folder = self._topic_folder(topic)
        file_name = f"{received_at[:10]}_{_slugify(title)}.md"
        file_path = folder / file_name

        if file_path.exists():
            file_name = f"{received_at[:10]}_{_slugify(title)}_{note_id[:8]}.md"
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
            "frontmatter_schema_version": 1,
            "title": title,
            "tags": tags.split(",") if tags else [],
            "topic": topic,
        }

        # YAML собираем через safe_dump: значения экранируются корректно
        # (заголовок с «: », кавычками и т. п. больше не ломает frontmatter).
        frontmatter_text = yaml.safe_dump(
            frontmatter,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        ).strip()
        lines: list[str] = ["---", frontmatter_text, "---", ""]
        lines.append(f"# {title}")
        lines.append("")
        lines.append(content)
        lines.append("")

        # Атомарная запись через временный файл
        tmp_path = file_path.with_suffix(".md.tmp")
        tmp_path.write_text("\n".join(lines), encoding="utf-8")
        tmp_path.replace(file_path)

        logger.info("Запись заметки: %s", file_path)
        return file_path

    def list_notes(self, topic: Optional[str] = None) -> list[Path]:
        """Возвращает список всех .md заметок, опционально по теме."""
        if topic:
            folder = self._topic_folder(topic)
            return sorted(folder.glob("*.md"))
        return sorted(self.vault_path.rglob("*.md"))

    def read_note(self, path: Path) -> str:
        """Читает содержимое заметки."""
        return path.read_text(encoding="utf-8")
