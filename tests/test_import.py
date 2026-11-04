"""Тесты импорта (P1-05)."""

from __future__ import annotations

from pathlib import Path

from kb_agent.core.import_core import CoreImporter, run_import_core
from kb_agent.storage.state import StateManager


class TestImportCore:
    """P1-05: import-core --apply реально создаёт заметки и индекс."""

    def test_import_creates_note_and_fts_index(self, config, state, temp_dir):
        """При apply файл импортируется в vault и индексируется в FTS5."""
        # Создаём исходный markdown-файл
        source_dir = temp_dir / "source"
        source_dir.mkdir()
        (source_dir / "test_note.md").write_text(
            "---\ntitle: Test Note\ntopic: Test\ntags: [test, import]\nsource: import\n---\n\n# Заголовок\n\nСодержимое заметки для теста импорта.",
            encoding="utf-8",
        )

        report = run_import_core(config, state, source_dir, dry_run=False)

        assert report.imported == 1
        assert report.errors == 0

        # Проверяем, что файл появился в vault
        vault_files = list((config.vault_path).rglob("*.md"))
        assert len(vault_files) == 1, f"Ожидался 1 файл в vault, найдено {len(vault_files)}"

        # Проверяем FTS индекс (ищем по слову из содержимого)
        results = state.search("Содержимое", limit=5)
        assert len(results) == 1
        assert results[0]["title"] == "Test Note"

    def test_import_idempotent_no_duplicates(self, config, state, temp_dir):
        """Повторный импорт того же файла не создаёт дублей."""
        source_dir = temp_dir / "source"
        source_dir.mkdir()
        (source_dir / "test_note.md").write_text(
            "---\ntitle: Test Note\ntopic: Test\ntags: [test]\nsource: import\n---\n\n# Заголовок\n\nСодержимое.",
            encoding="utf-8",
        )

        report1 = run_import_core(config, state, source_dir, dry_run=False)
        report2 = run_import_core(config, state, source_dir, dry_run=False)

        assert report1.imported == 1
        assert report2.imported == 0  # ничего нового не импортировано
        assert report2.skipped == 1  # пропущен как уже существующий

        # FTS не должен содержать дублей
        results = state.search("Содержимое", limit=10)
        assert len(results) == 1

    def test_dry_run_does_not_write(self, config, state, temp_dir):
        """Dry-run не создаёт файлы в vault и не индексирует."""
        source_dir = temp_dir / "source"
        source_dir.mkdir()
        (source_dir / "test_note.md").write_text(
            "---\ntitle: Dry Run Note\ntopic: Test\ntags: [test]\n---\n\nСодержимое.",
            encoding="utf-8",
        )

        report = run_import_core(config, state, source_dir, dry_run=True)

        assert report.imported == 1  # dry_run считает, что будет импортировано
        assert report.errors == 0

        # Но ничего не записано в vault
        vault_files = list((config.vault_path).rglob("*.md"))
        assert len(vault_files) == 0

        # И ничего в FTS
        results = state.search("Содержимое", limit=5)
        assert len(results) == 0