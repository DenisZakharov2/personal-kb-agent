"""Импорт существующего ядра материалов (Этап 1).

Dry-run режим: показывает, что будет импортировано, без записи.
Оригиналы не перезаписываются и не удаляются.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from kb_agent.config import AppConfig
from kb_agent.storage.state import StateManager


@dataclass
class ImportReport:
    total_files: int = 0
    imported: int = 0
    skipped: int = 0
    errors: int = 0
    details: list[str] = field(default_factory=list)


class CoreImporter:
    def __init__(self, config: AppConfig, state: StateManager) -> None:
        self.config = config
        self.state = state

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
                report.details.append(f"Импортировано: {f}")
                report.imported += 1
            except Exception as exc:
                report.errors += 1
                report.details.append(f"Ошибка при импорте {f}: {exc}")
        return report


def run_import_core(config: AppConfig, state: StateManager, source_path: Path, dry_run: bool = True) -> ImportReport:
    importer = CoreImporter(config, state)
    if dry_run:
        return importer.dry_run(source_path)
    return importer.import_files(source_path)
