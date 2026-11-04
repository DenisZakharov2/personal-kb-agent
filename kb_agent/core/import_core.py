"""Импорт существующего ядра материалов (Этап 1).

Реальный импорт: читает файлы из исходного каталога, копирует их в vault
с YAML frontmatter, индексирует в FTS5. Поддерживает dry-run и apply режимы.
Идемпотентен — повторный импорт не создаёт дублей (проверка по raw_hash).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from kb_agent.config import AppConfig
from kb_agent.storage.state import StateManager
from kb_agent.storage.vault import VaultManager
from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.core.import_core")


@dataclass
class ImportReport:
    total_files: int = 0
    imported: int = 0
    skipped: int = 0
    errors: int = 0
    details: list[str] = field(default_factory=list)


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Извлекает YAML frontmatter и возвращает (данные, тело без frontmatter)."""
    if not content.startswith("---\n"):
        return {}, content
    end = content.find("\n---\n", 4)
    if end == -1:
        return {}, content
    fm_text = content[4:end]
    try:
        data = yaml.safe_load(fm_text) or {}
        if not isinstance(data, dict):
            return {}, content
        body = content[end + 5:]
        return data, body
    except Exception:
        return {}, content


def _slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_]+", "_", text)
    return text[:80] or "Общее"


class CoreImporter:
    def __init__(self, config: AppConfig, state: StateManager) -> None:
        self.config = config
        self.state = state
        self.vault = VaultManager(config)

    def scan(self, source_path: Path) -> list[Path]:
        source_path = Path(source_path)
        if not source_path.exists():
            return []
        files: list[Path] = []
        for ext in ("*.md", "*.txt"):
            files.extend(source_path.rglob(ext))
        return sorted(files)

    def dry_run(self, source_path: Path) -> ImportReport:
        files = self.scan(source_path)
        report = ImportReport(total_files=len(files))
        for f in files:
            report.details.append(f"Будет импортировано: {f}")
            report.imported += 1
        return report

    def import_files(self, source_path: Path) -> ImportReport:
        files = self.scan(source_path)
        report = ImportReport(total_files=len(files))

        for f in files:
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
                fm, body = _parse_frontmatter(content)

                title = fm.get("title") or f.stem
                topic = fm.get("topic", "Общее")
                tags = fm.get("tags", [])
                if isinstance(tags, str):
                    tags = [t.strip() for t in tags.split(",") if t.strip()]
                tags_str = ",".join(tags)

                source_type = fm.get("source", "import")
                source_id = fm.get("source_id", f"import_{_slugify(f.stem)}")
                external_id = fm.get("external_id", "")
                canonical_url = fm.get("url", "")
                published_at = fm.get("published_at")
                received_at = fm.get("received_at") or datetime.now(timezone.utc).isoformat()

                # Дедуп по raw_hash — содержимое файла
                raw_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

                # Проверяем, не импортирован ли уже (по raw_hash в raw_queue)
                existing = self.state.connect().execute(
                    "SELECT 1 FROM raw_queue WHERE raw_hash = ?", (raw_hash,)
                ).fetchone()
                if existing:
                    report.skipped += 1
                    report.details.append(f"Уже импортировано (пропуск): {f}")
                    continue

                # Пишем в vault
                note_id = f"{received_at[:13]}_{hashlib.sha256(f.name.encode()).hexdigest()[:8]}"

                file_path = self.vault.write_note(
                    note_id=note_id,
                    source_id=source_id,
                    source_type=source_type,
                    external_id=external_id,
                    canonical_url=canonical_url,
                    published_at=published_at,
                    received_at=received_at,
                    title=title,
                    tags=tags_str,
                    topic=topic,
                    content=body.strip(),
                )

                # Индексируем в FTS5
                self.state.index_note(
                    note_id=note_id,
                    title=title,
                    tags=tags_str,
                    topic=topic,
                    content=body.strip(),
                )

                # Добавляем в raw_queue как processed (чтобы не собирать повторно)
                self.state.enqueue(
                    source_id=source_id,
                    external_id=external_id or f"import_{note_id}",
                    canonical_url=canonical_url,
                    title=title,
                    raw_text=body.strip(),
                    raw_hash=raw_hash,
                    published_at=published_at,
                )

                report.imported += 1
                report.details.append(f"Импортировано: {f} → {file_path}")

            except Exception as exc:
                report.errors += 1
                report.details.append(f"Ошибка при импорте {f}: {exc}")
                logger.exception("Ошибка импорта %s", f)

        return report


def run_import_core(
    config: AppConfig,
    state: StateManager,
    source_path: Path,
    dry_run: bool = True,
) -> ImportReport:
    importer = CoreImporter(config, state)
    if dry_run:
        return importer.dry_run(source_path)
    return importer.import_files(source_path)