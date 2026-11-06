"""Тесты CLI-команды `kb-agent import` (раздел 9 аудита: импорт Markdown/TXT)."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

from typer.testing import CliRunner

from kb_agent.cli.main import app

import_mod = importlib.import_module("kb_agent.cli.commands.import_cmd")


def _write_note(src: Path, name: str, fm: dict, body: str) -> None:
    lines = ["---"]
    import yaml

    lines.append(yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip())
    lines.append("---")
    lines.append("")
    lines.append(f"# {fm.get('title', name)}")
    lines.append("")
    lines.append(body)
    (src / name).write_text("\n".join(lines), encoding="utf-8")


class TestImportCommand:
    def test_import_creates_notes_and_fts(self, config, state, temp_dir, monkeypatch):
        monkeypatch.setattr("kb_agent.cli.commands.import_cmd.load_config", lambda: config)
        src = temp_dir / "src"
        src.mkdir()
        _write_note(
            src,
            "note1.md",
            {
                "title": "Первая заметка",
                "topic": "Тест",
                "tags": ["тэг1"],
                "source": "import",
                "source_id": "import_src",
                "external_id": "ext1",
                "url": "https://example.com/1",
            },
            "Содержимое первой заметки про нейросети и автоматизацию.",
        )

        runner = CliRunner()
        result = runner.invoke(app, ["import", str(src), "--json"], prog_name="kb-agent")
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["imported"] == 1
        assert data["errors"] == 0

        found = state.search("нейросети", limit=5)
        assert len(found) == 1
        assert found[0]["title"] == "Первая заметка"

        raw = state.get_raw_content(found[0]["note_id"])
        assert raw is not None
        assert "нейросети" in raw["raw_content"]

    def test_import_idempotent_no_duplicates(self, config, state, temp_dir, monkeypatch):
        monkeypatch.setattr("kb_agent.cli.commands.import_cmd.load_config", lambda: config)
        src = temp_dir / "src"
        src.mkdir()
        _write_note(
            src,
            "note1.md",
            {
                "title": "Идемпотент",
                "topic": "Тест",
                "tags": [],
                "source": "import",
                "source_id": "import_src",
                "external_id": "ext_idem",
                "url": "https://example.com/idem",
            },
            "Содержимое для проверки идемпотентности.",
        )

        runner = CliRunner()
        r1 = runner.invoke(app, ["import", str(src), "--json"], prog_name="kb-agent")
        assert r1.exit_code == 0
        r2 = runner.invoke(app, ["import", str(src), "--json"], prog_name="kb-agent")
        assert r2.exit_code == 0
        data2 = json.loads(r2.output)
        assert data2["imported"] == 0
        assert data2["skipped"] == 1

        found = state.search("идемпотентности", limit=5)
        assert len(found) == 1

    def test_import_updates_existing_note_on_source_change(self, config, state, temp_dir, monkeypatch):
        monkeypatch.setattr("kb_agent.cli.commands.import_cmd.load_config", lambda: config)
        src = temp_dir / "src"
        src.mkdir()
        _write_note(
            src,
            "note1.md",
            {
                "title": "Обновляемая",
                "topic": "Тест",
                "tags": [],
                "source": "import",
                "source_id": "import_src",
                "external_id": "ext_update",
                "url": "https://example.com/update",
            },
            "Первая версия содержимого.",
        )
        runner = CliRunner()
        runner.invoke(app, ["import", str(src), "--json"], prog_name="kb-agent")

        # Меняем содержимое того же источника (тот же external_id/source_id)
        _write_note(
            src,
            "note1.md",
            {
                "title": "Обновляемая",
                "topic": "Тест",
                "tags": [],
                "source": "import",
                "source_id": "import_src",
                "external_id": "ext_update",
                "url": "https://example.com/update",
            },
            "Вторая версия: обновлённое содержимое про ИИ.",
        )
        r = runner.invoke(app, ["import", str(src), "--json"], prog_name="kb-agent")
        assert r.exit_code == 0
        data = json.loads(r.output)
        assert data["updated"] == 1
        assert data["imported"] == 0

        notes = state.connect().execute(
            "SELECT note_id, revision FROM notes WHERE external_id = ?", ("ext_update",)
        ).fetchall()
        assert len(notes) == 1
        assert notes[0]["revision"] == 2

        # FTS не дублируется
        fts_rows = state.connect().execute(
            "SELECT COUNT(*) AS c FROM notes_fts WHERE note_id = ?", (notes[0]["note_id"],)
        ).fetchone()
        assert fts_rows["c"] == 1

    def test_import_dry_run_does_not_write(self, config, state, temp_dir, monkeypatch):
        monkeypatch.setattr("kb_agent.cli.commands.import_cmd.load_config", lambda: config)
        src = temp_dir / "src"
        src.mkdir()
        _write_note(
            src,
            "note1.md",
            {"title": "Dry Run", "topic": "Тест", "tags": [], "source": "import"},
            "Содержимое dry-run.",
        )

        runner = CliRunner()
        result = runner.invoke(
            app, ["import", str(src), "--dry-run", "--json"], prog_name="kb-agent"
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["imported"] == 1
        assert len(list((config.vault_path).rglob("*.md"))) == 0
        assert state.search("dry-run", limit=5) == []

    def test_import_nonexistent_path(self, config, state, temp_dir, monkeypatch):
        monkeypatch.setattr("kb_agent.cli.commands.import_cmd.load_config", lambda: config)
        runner = CliRunner()
        result = runner.invoke(
            app, ["import", str(temp_dir / "does_not_exist")], prog_name="kb-agent"
        )
        assert result.exit_code == 0
        assert "файлов 0" in result.output
