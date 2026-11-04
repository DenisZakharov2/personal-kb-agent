"""Менеджер хранилища Obsidian vault (Этап 2/5).

Безопасная запись заметок .md с YAML frontmatter в тематические папки
(по темам из профиля пользователя, а не по источнику).
Поддерживает обновление существующей заметки по note_id и архивацию
(перемещение в папку Архив).
"""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path
from typing import Optional

import yaml

from kb_agent.config import AppConfig
from kb_agent.utils.logger import setup_logger
from kb_agent.utils.slug import slugify

logger = setup_logger("kb_agent.storage.vault")


def atomic_write_text(path: Path, text: str, backup: bool = True) -> None:
    """Атомарная запись текста в файл.

    Запись ведётся во временный файл в той же директории, затем
    ``os.replace`` заменяет целевой файл — целевой файл никогда не остаётся
    частично записанным при падении процесса. Если ``backup=True`` и целевой
    файл существует, предварительно создаётся копия с расширением ``.bak``,
    чтобы не потерять пользовательское содержимое при перезаписи (P2-09).
    """
    path = Path(path)
    if backup and path.exists():
        backup_path = path.with_name(path.name + ".bak")
        backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, path)


class VaultManager:
    """Работа с файловой системой Obsidian vault."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.vault_path = Path(config.vault_path)
        self.vault_path.mkdir(parents=True, exist_ok=True)

    def _topic_folder(self, topic: str) -> Path:
        """Папка по тематике (из профиля или 'Общее')."""
        folder_name = slugify(topic)
        folder = self.vault_path / folder_name
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def find_note_file(self, note_id: str) -> Optional[Path]:
        """Находит файл заметки по note_id из frontmatter.

        Сначала ищет точное совпадение note_id, чтобы при обновлении
        существующей заметки перезаписать тот же файл (без дублей).
        """
        for path in self.list_notes():
            try:
                parsed = self.parse_note(path)
            except Exception:  # noqa: BLE001
                logger.warning("Не удалось разобрать frontmatter %s, пропускаю", path)
                continue
            if parsed.get("note_id") == note_id:
                return path
        return None

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
        archived: bool = False,
    ) -> Path:
        """Создаёт или обновляет .md файл с YAML frontmatter.

        Если файл с таким ``note_id`` уже существует (по frontmatter),
        запись выполняется поверх него — идемпотентное обновление при
        повторном сборе того же источника не создаёт дублей файлов.
        В противном случае создаётся новый файл в тематической папке
        (или в папке Архив, если ``archived=True``).

        ``previous_note_id`` зарезервирован для будущего версионирования
        заметок (актуализация при изменении источника).
        """
        existing = self.find_note_file(note_id)
        if existing is not None:
            file_path = existing
            # Тема берём из существующего файла, если не передана явно через topic
            folder = file_path.parent
        else:
            if archived:
                folder = self.vault_path / "Архив"
                folder.mkdir(parents=True, exist_ok=True)
            else:
                folder = self._topic_folder(topic)
            file_name = f"{received_at[:10]}_{slugify(title)}.md"
            file_path = folder / file_name
            if file_path.exists():
                file_name = f"{received_at[:10]}_{slugify(title)}_{note_id[:8]}.md"
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
            "archived": archived,
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

    def archive_note(self, note_id: str) -> Optional[Path]:
        """Перемещает файл заметки в папку ``Архив`` и возвращает новый путь.

        Если файл не найден — возвращает None. Повторный вызов для уже
        архивной заметки безопасен (идемпотентен): если файл уже в архиве,
        возвращается его текущий путь без перемещения.
        """
        path = self.find_note_file(note_id)
        if path is None:
            return None
        if path.parent.name == "Архив":
            logger.info("Заметка уже в архиве: %s", path)
            return path
        archive_folder = self.vault_path / "Архив"
        archive_folder.mkdir(parents=True, exist_ok=True)
        target = archive_folder / path.name
        if target.exists():
            target = (
                archive_folder / f"{path.stem}_{uuid.uuid4().hex[:8]}{path.suffix}"
            )
        os.replace(path, target)
        # Помечаем в frontmatter, чтобы заметка не потеряла статус архивной
        self._rewrite_frontmatter(target, archived=True)
        logger.info("Заметка archived: %s -> %s", path, target)
        return target

    def merge_notes_files(self, keep_note_id: str, drop_note_id: str) -> dict:
        """Синхронизирует файлы двух заметок при слиянии.

        * У файла основной заметки (``keep_note_id``) во frontmatter добавляется
          поле ``merged_from`` со списком архивированных дублей.
        * Файл дубля (``drop_note_id``) перемещается в папку ``Архив`` через
          ``archive_note`` (идемпотентно: если уже в архиве — остаётся на месте).

        Возвращает отчёт с путями файлов.
        """
        keep_path = self.find_note_file(keep_note_id)
        drop_path = self.find_note_file(drop_note_id)

        report: dict = {
            "keep_note_id": keep_note_id,
            "drop_note_id": drop_note_id,
            "keep_file": str(keep_path) if keep_path else None,
            "drop_file": str(drop_path) if drop_path else None,
            "drop_archived_to": None,
            "frontmatter_updated": False,
        }

        # Основной файл: фиксируем в frontmatter, из какого дубля слияние.
        if keep_path is not None:
            self._add_merged_from(keep_path, drop_note_id)
            report["frontmatter_updated"] = True

        # Файл дубля — в архив (безопасно, если уже там).
        if drop_path is not None:
            archived_path = self.archive_note(drop_note_id)
            report["drop_archived_to"] = str(archived_path) if archived_path else None

        return report

    def _add_merged_from(self, path: Path, merged_note_id: str) -> None:
        """Добавляет ``note_id`` дубля в поле ``merged_from`` frontmatter файла."""
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            return
        _, _, rest = text.partition("---\n")
        frontmatter_text, _, body = rest.partition("---\n")
        try:
            loaded = yaml.safe_load(frontmatter_text) or {}
        except yaml.YAMLError:  # noqa: BLE001
            return
        if not isinstance(loaded, dict):
            return
        merged_from = loaded.get("merged_from") or []
        if not isinstance(merged_from, list):
            merged_from = [merged_from] if merged_from else []
        merged_from = [str(m) for m in merged_from if str(m).strip()]
        if merged_note_id not in merged_from:
            merged_from.append(merged_note_id)
        loaded["merged_from"] = merged_from
        new_fm = (
            yaml.safe_dump(loaded, allow_unicode=True, sort_keys=False, default_flow_style=False).strip()
        )
        path.write_text(
            "---\n" + new_fm + "\n---\n" + body, encoding="utf-8"
        )

    def restore_note(self, note_id: str, topic: Optional[str] = None) -> Optional[Path]:
        """Возвращает файл заметки из архива в тематическую папку.

        ``topic`` — целевая тема (по умолчанию берётся из frontmatter).
        Возвращает новый путь или None, если файл не найден.
        """
        path = self.find_note_file(note_id)
        if path is None:
            # Ищем в корне и в архиве (list_notes обходит все подпапки vault)
            return None
        if path.parent.name == "Архив":
            topic = topic or (self.parse_note(path).get("topic") or "Общее")
            folder = self._topic_folder(topic)
            target = folder / path.name
            if target.exists():
                target = (
                    folder / f"{path.stem}_{uuid.uuid4().hex[:8]}{path.suffix}"
                )
            os.replace(path, target)
            self._rewrite_frontmatter(target, archived=False)
            logger.info("Заметка restored: %s -> %s", path, target)
            return target
        return path

    @staticmethod
    def _rewrite_frontmatter(path: Path, archived: bool) -> None:
        """Перезаписывает только поле ``archived`` во frontmatter существующего файла."""
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n"):
            return
        _, _, rest = text.partition("---\n")
        frontmatter_text, _, body = rest.partition("---\n")
        try:
            loaded = yaml.safe_load(frontmatter_text) or {}
        except yaml.YAMLError:  # noqa: BLE001
            return
        if not isinstance(loaded, dict):
            return
        loaded["archived"] = archived
        new_fm = yaml.safe_dump(loaded, allow_unicode=True, sort_keys=False, default_flow_style=False).strip()
        path.write_text(
            "---\n" + new_fm + "\n---\n" + body, encoding="utf-8"
        )

    def list_notes(self, topic: Optional[str] = None) -> list[Path]:
        """Возвращает список всех .md заметок, опционально по теме."""
        if topic:
            folder = self._topic_folder(topic)
            return sorted(folder.glob("*.md"))
        return sorted(self.vault_path.rglob("*.md"))

    def read_note(self, path: Path) -> str:
        """Читает содержимое заметки."""
        return path.read_text(encoding="utf-8")

    def parse_note(self, path: Path) -> dict:
        """Разбирает .md-файл заметки на frontmatter и тело.

        Возвращает словарь с полями: note_id, title, tags, topic, source,
        source_id, external_id, url, published_at, received_at, date_saved,
        previous_note_id, frontmatter_schema_version, content, content_hash.
        Если note_id отсутствует во frontmatter, возвращается пустая строка —
        caller должен сгенерировать идентификатор самостоятельно.
        """
        text = path.read_text(encoding="utf-8")
        data: dict = {}
        body = text

        if text.startswith("---\n"):
            _, _, rest = text.partition("---\n")
            frontmatter_text, _, body = rest.partition("---\n")
            try:
                loaded = yaml.safe_load(frontmatter_text)
                if isinstance(loaded, dict):
                    data = loaded
            except yaml.YAMLError as exc:  # noqa: BLE001
                logger.warning("Не удалось разобрать frontmatter %s: %s", path, exc)

        tags = data.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        elif not isinstance(tags, list):
            tags = [tags] if tags is not None else []
        tags = [str(t).strip() for t in tags if str(t).strip()]

        content = body.strip()
        return {
            "note_id": data.get("note_id") or "",
            "title": data.get("title") or path.stem,
            "tags": ",".join(tags),
            "topic": data.get("topic") or "Общее",
            "source": data.get("source", ""),
            "source_id": data.get("source_id") or "",
            "external_id": data.get("external_id") or "",
            "url": data.get("url") or data.get("canonical_url") or "",
            "published_at": data.get("published_at"),
            "received_at": data.get("received_at"),
            "date_saved": data.get("date_saved"),
            "previous_note_id": data.get("previous_note_id"),
            "frontmatter_schema_version": data.get("frontmatter_schema_version"),
            "content": content,
            "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
