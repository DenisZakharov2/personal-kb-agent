"""Волна 1: kb-agent reindex — перестройка notes_fts из файлов vault (P2-05)."""

from __future__ import annotations

import json

from typer.testing import CliRunner

import kb_agent.cli.commands.process as process_mod
from kb_agent.cli.main import app


def _write_note(vault_path, note_id, title, topic, tags, content, file_name):
    folder = vault_path / topic
    folder.mkdir(parents=True, exist_ok=True)
    frontmatter = (
        "---\n"
        f"title: {title}\n"
        f"topic: {topic}\n"
        f"tags: [{tags}]\n"
        f"source_id: tg_chan\n"
        f"external_id: ext-{note_id}\n"
        f"url: https://t.me/chan/{note_id}\n"
        f"note_id: {note_id}\n"
        "---\n\n"
        f"# {title}\n\n{content}\n"
    )
    (folder / file_name).write_text(frontmatter, encoding="utf-8")


class TestReindex:
    def test_reindex_creates_notes_and_fts(self, config, state, monkeypatch):
        """reindex читает frontmatter и пишет в notes + notes_fts."""
        monkeypatch.setattr(process_mod, "load_config", lambda: config)

        _write_note(
            config.vault_path,
            note_id="note-reindex-1",
            title="Заметка для реиндекса",
            topic="Тема",
            tags="тест, реиндекс",
            content="Уникальный контент для проверки переиндексации.",
            file_name="2026-01-01_Заметка.md",
        )

        runner = CliRunner()
        result = runner.invoke(app, ["reindex", "--json"], prog_name="kb-agent")
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["indexed"] >= 1

        row = state.get_note("note-reindex-1")
        assert row is not None
        assert row["title"] == "Заметка для реиндекса"
        assert row["topic"] == "Тема"
        assert row["source_id"] == "tg_chan"
        assert row["external_id"] == "ext-note-reindex-1"
        assert row["file_path"] is not None
        assert row["content_hash"] is not None
        assert row["revision"] >= 1

        found = state.search("переиндексации", limit=5)
        assert len(found) == 1
        assert found[0]["note_id"] == "note-reindex-1"

    def test_reindex_is_rebuildable_and_removes_orphan_fts(self, config, state, monkeypatch):
        """FTS — rebuildable проекция: повторный reindex не дублит, мёртвые записи удаляются."""
        monkeypatch.setattr(process_mod, "load_config", lambda: config)

        _write_note(
            config.vault_path,
            note_id="note-rebuild-1",
            title="Заметка один",
            topic="Тема",
            tags="тест",
            content="Содержимое первой заметки.",
            file_name="2026-01-01_Один.md",
        )

        runner = CliRunner()
        r1 = runner.invoke(app, ["reindex", "--json"], prog_name="kb-agent")
        assert r1.exit_code == 0

        # Мёртвая FTS-запись без файла должна быть удалена при перестройке.
        conn = state.connect()
        conn.execute(
            "INSERT INTO notes_fts (note_id, title, tags, topic, content) VALUES (?, ?, ?, ?, ?)",
            ("ghost-note", "Призрак", "", "", "призрачный контент"),
        )
        conn.commit()

        r2 = runner.invoke(app, ["reindex", "--json"], prog_name="kb-agent")
        assert r2.exit_code == 0

        row = state.get_note("note-rebuild-1")
        assert row["revision"] == 1  # содержимое не менялось — revision не растёт
        found = state.search("призрачный", limit=5)
        assert found == []
        found = state.search("первой", limit=5)
        assert len(found) == 1

    def test_reindex_note_without_note_id(self, config, state, monkeypatch):
        """Файл без note_id во frontmatter получает сгенерированный идентификатор."""
        monkeypatch.setattr(process_mod, "load_config", lambda: config)

        (config.vault_path / "Тема").mkdir(parents=True)
        (config.vault_path / "Тема" / "2026-02-02_Без_ID.md").write_text(
            "---\ntitle: Без ID\ntopic: Тема\n---\n\nТекст без идентификатора.\n",
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(app, ["reindex", "--json"], prog_name="kb-agent")
        assert result.exit_code == 0, result.output

        notes = state.connect().execute("SELECT note_id FROM notes").fetchall()
        assert len(notes) == 1
        assert notes[0]["note_id"]  # сгенерированный идентификатор не пустой

    def test_reindex_revision_increments_on_content_change(self, config, state, monkeypatch):
        """При изменении содержимого файла revision увеличивается."""
        monkeypatch.setattr(process_mod, "load_config", lambda: config)

        _write_note(
            config.vault_path,
            note_id="note-change-1",
            title="Заголовок",
            topic="Тема",
            tags="тест",
            content="Версия один.",
            file_name="2026-03-01_Один.md",
        )

        runner = CliRunner()
        r1 = runner.invoke(app, ["reindex", "--json"], prog_name="kb-agent")
        assert r1.exit_code == 0

        revision_after_first = state.get_note("note-change-1")["revision"]

        # Меняем содержимое того же файла (тот же note_id)
        _write_note(
            config.vault_path,
            note_id="note-change-1",
            title="Заголовок",
            topic="Тема",
            tags="тест",
            content="Версия два — обновлённое содержимое.",
            file_name="2026-03-01_Один.md",
        )

        r2 = runner.invoke(app, ["reindex", "--json"], prog_name="kb-agent")
        assert r2.exit_code == 0

        revision_after_second = state.get_note("note-change-1")["revision"]
        assert revision_after_second == revision_after_first + 1

    def test_reindex_removes_notes_row_for_deleted_file(self, config, state, monkeypatch):
        """При удалении файла из vault запись удаляется и из notes, и из notes_fts."""
        monkeypatch.setattr(process_mod, "load_config", lambda: config)

        _write_note(
            config.vault_path,
            note_id="note-delete-1",
            title="Будет удалён",
            topic="Тема",
            tags="тест",
            content="Контент для удаления.",
            file_name="2026-04-01_Удалить.md",
        )

        runner = CliRunner()
        r1 = runner.invoke(app, ["reindex", "--json"], prog_name="kb-agent")
        assert r1.exit_code == 0

        # Удаляем файл из vault
        file_path = config.vault_path / "Тема" / "2026-04-01_Удалить.md"
        file_path.unlink()

        r2 = runner.invoke(app, ["reindex", "--json"], prog_name="kb-agent")
        assert r2.exit_code == 0

        # Записи должны быть удалены из обеих таблиц
        assert state.get_note("note-delete-1") is None
        found = state.search("удаления", limit=5)
        assert found == []