"""Генератор Каталога.md (Этап 2/5).

Автоматическая пересборка Каталог.md на основе заметок в vault.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import yaml

from kb_agent.storage.vault import VaultManager, atomic_write_text
from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.storage.catalog")


def _slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_]+", "_", text)
    return text[:80] or "Общее"


class CatalogGenerator:
    """Генерирует Каталог.md на основе заметок в vault."""

    def __init__(self, vault_manager: VaultManager) -> None:
        self.vault = vault_manager

    @staticmethod
    def _parse_frontmatter(content: str, note_path: Optional[Path] = None) -> dict:
        if not content.startswith("---\n"):
            return {}
        end = content.find("\n---\n", 4)
        if end == -1:
            return {}
        fm_text = content[4:end]
        try:
            return yaml.safe_load(fm_text) or {}
        except Exception as exc:  # noqa: BLE001
            # Не молча скрываем битый frontmatter — предупреждаем, но не
            # прерываем генерацию каталога из-за одной заметки (P2-09).
            logger.warning(
                "Не удалось разобрать frontmatter %s: %s",
                note_path or "<неизвестно>",
                exc,
            )
            return {}

    def generate(self, output_path: Optional[Path] = None) -> Path:
        """Генерирует Каталог.md и возвращает путь к нему."""
        vault_path = self.vault.vault_path
        if output_path is None:
            output_path = vault_path / "Каталог.md"

        # Служебные файлы не являются заметками и не должны попадать в каталог
        generated_files = {"Каталог.md", "Дайджест.md"}
        notes = [
            note_path
            for note_path in self.vault.list_notes()
            if note_path.name not in generated_files
        ]

        # Группируем по темам
        by_topic: dict[str, list[tuple[str, str, str]]] = {}  # topic -> [(date, title, rel_path)]
        for note_path in notes:
            content = note_path.read_text(encoding="utf-8")
            fm = self._parse_frontmatter(content, note_path)
            topic = fm.get("topic", "Общее")
            title = fm.get("title", note_path.stem)
            date = fm.get("date_saved", "")
            # Приводим дату к строке (YAML может распарсить как date объект)
            if hasattr(date, "isoformat"):
                date = date.isoformat()
            by_topic.setdefault(topic, []).append((date, title, str(note_path.relative_to(vault_path))))

        lines: list[str] = []
        lines.append("# Каталог базы знаний")
        lines.append("")
        lines.append(f"Всего заметок: {len(notes)}")
        lines.append("")
        lines.append("## По темам")
        lines.append("")

        for topic in sorted(by_topic.keys()):
            items = sorted(by_topic[topic], key=lambda x: x[0], reverse=True)
            lines.append(f"### {topic}")
            lines.append("")
            for date, title, rel_path in items:
                lines.append(f"- [{title}]({rel_path}) — {date}")
            lines.append("")

        atomic_write_text(output_path, "\n".join(lines), backup=True)
        logger.info("Каталог сгенерирован: %s (%s заметок)", output_path, len(notes))
        return output_path
