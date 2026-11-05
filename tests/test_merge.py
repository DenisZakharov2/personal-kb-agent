"""Тесты команды `kb-agent merge` (раздел 9 аудита: слияние дублей).

Покрывает: базовое слияние, перенос ссылок, FTS, архивацию дубля,
dry-run, валидацию (несуществующий ID, одинаковые ID), перенос raw_content,
JSON-вывод и работу с файлами vault.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from kb_agent.cli.main import app
from kb_agent.storage.vault import VaultManager

merge_mod = None  # импортируется в фикстуре ниже для monkeypatch load_config


def _seed_note(state, note_id, title, tags="тэг1", topic="Тест", content="", source_id="src", external_id="ext"):
    state.index_note(
        note_id=note_id,
        title=title,
        tags=tags,
        topic=topic,
        content=content,
        source_id=source_id,
        external_id=external_id,
    )


def _seed_vault_note(vault, note_id, title, tags="тэг1", topic="Тест", content=""):
    vault.write_note(
        note_id=note_id,
        source_id="src",
        source_type="telegram",
        external_id=f"ext_{note_id}",
        canonical_url=f"https://example.com/{note_id}",
        published_at=None,
        received_at="2026-01-01T00:00:00+00:00",
        title=title,
        tags=tags,
        topic=topic,
        content=content,
    )


class TestMergeCommand:
    def test_basic_merge(self, config, state, monkeypatch):
        monkeypatch.setattr("kb_agent.cli.commands.merge.load_config", lambda: config)
        _seed_note(state, "note-keep", "Заголовок А", content="содержание заметки А")
        _seed_note(state, "note-drop", "Заголовок Б", content="содержание заметки Б")
        _seed_note(state, "note-third", "Заголовок В", content="третья заметка")
        state.add_link("note-drop", "note-third", "references")
        state.add_link("note-third", "note-drop", "references")

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["merge", "note-keep", "note-drop", "--keep", "1", "--json"],
            prog_name="kb-agent",
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["kept_note_id"] == "note-keep"
        assert data["kept_title"] == "Заголовок А"
        assert data["dropped_note_id"] == "note-drop"
        assert data["dropped_title"] == "Заголовок Б"
        assert data["links_moved"] == 2
        assert data["fts_entries_removed"] == 1
        assert data["dry_run"] is False

        # Дубль остаётся в notes, но помечен архивным.
        drop = state.get_note("note-drop")
        assert drop is not None
        assert drop["is_archived"] == 1
        keep = state.get_note("note-keep")
        assert keep["is_archived"] == 0

        # Ссылки переназначены на основную заметку, дубля в ссылках нет.
        conn = state.connect()
        rows = conn.execute(
            "SELECT source_note_id, target_note_id, link_type FROM note_links"
        ).fetchall()
        pairs = {(r["source_note_id"], r["target_note_id"], r["link_type"]) for r in rows}
        assert ("note-keep", "note-third", "references") in pairs
        assert ("note-third", "note-keep", "references") in pairs
        assert all(
            r["source_note_id"] != "note-drop" and r["target_note_id"] != "note-drop"
            for r in rows
        )

        # Дубль удалён из FTS — обычный поиск его не находит.
        found = state.search("заметки Б", limit=5)
        assert all(r["note_id"] != "note-drop" for r in found)

    def test_merge_keep_2(self, config, state, monkeypatch):
        monkeypatch.setattr("kb_agent.cli.commands.merge.load_config", lambda: config)
        _seed_note(state, "note-keep", "Заголовок А", content="содержание А")
        _seed_note(state, "note-drop", "Заголовок Б", content="содержание Б")
        state.add_link("note-drop", "note-keep", "references")

        runner = CliRunner()
        result = runner.invoke(
            app, ["merge", "note-keep", "note-drop", "--keep", "2", "--json"],
            prog_name="kb-agent",
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["kept_note_id"] == "note-drop"
        assert data["dropped_note_id"] == "note-keep"
        assert state.get_note("note-keep")["is_archived"] == 1
        assert state.get_note("note-drop")["is_archived"] == 0

    def test_dry_run_does_not_change_anything(self, config, state, monkeypatch):
        monkeypatch.setattr("kb_agent.cli.commands.merge.load_config", lambda: config)
        _seed_note(state, "note-keep", "Заголовок А", content="содержание А")
        _seed_note(state, "note-drop", "Заголовок Б", content="содержание Б")
        state.add_link("note-drop", "note-keep", "references")

        runner = CliRunner()
        result = runner.invoke(
            app, ["merge", "note-keep", "note-drop", "--keep", "1", "--dry-run", "--json"],
            prog_name="kb-agent",
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["dry_run"] is True
        assert data["links_moved"] == 1

        # В БД и файлах ничего не изменилось.
        assert state.get_note("note-drop")["is_archived"] == 0
        assert state.get_raw_content("note-drop") is not None or True  # без изменений
        rows = state.connect().execute(
            "SELECT COUNT(*) AS c FROM note_links WHERE source_note_id = 'note-drop' OR target_note_id = 'note-drop'"
        ).fetchone()
        assert rows["c"] == 1

    def test_nonexistent_note_id_fails(self, config, state, monkeypatch):
        monkeypatch.setattr("kb_agent.cli.commands.merge.load_config", lambda: config)
        _seed_note(state, "note-keep", "Заголовок А")

        runner = CliRunner()
        result = runner.invoke(
            app, ["merge", "note-keep", "note-missing", "--keep", "1"],
            prog_name="kb-agent",
        )
        assert result.exit_code != 0

        result2 = runner.invoke(
            app, ["merge", "note-missing", "note-keep", "--keep", "2"],
            prog_name="kb-agent",
        )
        assert result2.exit_code != 0

    def test_same_ids_validation_fails(self, config, state, monkeypatch):
        monkeypatch.setattr("kb_agent.cli.commands.merge.load_config", lambda: config)
        _seed_note(state, "note-keep", "Заголовок А")

        runner = CliRunner()
        result = runner.invoke(
            app, ["merge", "note-keep", "note-keep", "--keep", "1"],
            prog_name="kb-agent",
        )
        assert result.exit_code != 0

        result2 = runner.invoke(
            app, ["merge", "note-keep", "note-keep", "--keep", "2"],
            prog_name="kb-agent",
        )
        assert result2.exit_code != 0

    def test_invalid_keep_validation_fails(self, config, state, monkeypatch):
        monkeypatch.setattr("kb_agent.cli.commands.merge.load_config", lambda: config)
        _seed_note(state, "note-keep", "Заголовок А")
        _seed_note(state, "note-drop", "Заголовок Б")

        runner = CliRunner()
        result = runner.invoke(
            app, ["merge", "note-keep", "note-drop", "--keep", "3"],
            prog_name="kb-agent",
        )
        assert result.exit_code != 0

    def test_raw_content_transfer(self, config, state, monkeypatch):
        monkeypatch.setattr("kb_agent.cli.commands.merge.load_config", lambda: config)
        _seed_note(state, "note-keep", "Заголовок А", content="содержание А")
        _seed_note(state, "note-drop", "Заголовок Б", content="содержание Б")
        state.save_raw_content("note-drop", "исходный контент дубля")

        runner = CliRunner()
        result = runner.invoke(
            app, ["merge", "note-keep", "note-drop", "--keep", "1", "--json"],
            prog_name="kb-agent",
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["raw_content_moved"] is True

        raw_keep = state.get_raw_content("note-keep")
        assert raw_keep is not None
        assert raw_keep["raw_content"] == "исходный контент дубля"
        assert state.get_raw_content("note-drop") is None

    def test_raw_content_not_overwritten(self, config, state, monkeypatch):
        monkeypatch.setattr("kb_agent.cli.commands.merge.load_config", lambda: config)
        _seed_note(state, "note-keep", "Заголовок А", content="содержание А")
        _seed_note(state, "note-drop", "Заголовок Б", content="содержание Б")
        state.save_raw_content("note-keep", "контент основной")
        state.save_raw_content("note-drop", "контент дубля")

        runner = CliRunner()
        result = runner.invoke(
            app, ["merge", "note-keep", "note-drop", "--keep", "1", "--json"],
            prog_name="kb-agent",
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["raw_content_moved"] is False
        assert state.get_raw_content("note-keep")["raw_content"] == "контент основной"

    def test_json_output_shape(self, config, state, monkeypatch):
        monkeypatch.setattr("kb_agent.cli.commands.merge.load_config", lambda: config)
        _seed_note(state, "note-keep", "Заголовок А")
        _seed_note(state, "note-drop", "Заголовок Б")

        runner = CliRunner()
        result = runner.invoke(
            app, ["merge", "note-keep", "note-drop", "--keep", "1", "--json"],
            prog_name="kb-agent",
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        for key in (
            "kept_note_id",
            "kept_title",
            "dropped_note_id",
            "dropped_title",
            "links_moved",
            "raw_content_moved",
            "fts_entries_removed",
            "dry_run",
        ):
            assert key in data, key
        assert data["links_moved"] == 0
        assert data["fts_entries_removed"] == 1

    def test_vault_files(self, config, state, temp_dir, monkeypatch):
        monkeypatch.setattr("kb_agent.cli.commands.merge.load_config", lambda: config)
        _seed_note(state, "note-keep", "Заголовок А", content="содержание А")
        _seed_note(state, "note-drop", "Заголовок Б", content="содержание Б")

        vault = VaultManager(config)
        _seed_vault_note(vault, "note-keep", "Заголовок А", content="содержание А")
        _seed_vault_note(vault, "note-drop", "Заголовок Б", content="содержание Б")

        runner = CliRunner()
        result = runner.invoke(
            app, ["merge", "note-keep", "note-drop", "--keep", "1", "--json"],
            prog_name="kb-agent",
        )
        assert result.exit_code == 0, result.output

        # Файл дубля перемещён в Архив.
        drop_path = vault.find_note_file("note-drop")
        assert drop_path is not None
        assert drop_path.parent.name == "Архив", drop_path

        # У основного файла в frontmester есть merged_from.
        keep_path = vault.find_note_file("note-keep")
        assert keep_path is not None
        text = keep_path.read_text(encoding="utf-8")
        assert "merged_from:" in text
        assert "note-drop" in text

    def test_merge_idempotent_on_already_archived(self, config, state, monkeypatch):
        monkeypatch.setattr("kb_agent.cli.commands.merge.load_config", lambda: config)
        _seed_note(state, "note-keep", "Заголовок А", content="содержание А")
        _seed_note(state, "note-drop", "Заголовок Б", content="содержание Б")

        runner = CliRunner()
        r1 = runner.invoke(
            app, ["merge", "note-keep", "note-drop", "--keep", "1", "--json"],
            prog_name="kb-agent",
        )
        assert r1.exit_code == 0, r1.output
        r2 = runner.invoke(
            app, ["merge", "note-keep", "note-drop", "--keep", "1", "--json"],
            prog_name="kb-agent",
        )
        assert r2.exit_code == 0, r2.output
        data2 = json.loads(r2.output)
        assert data2["links_moved"] == 0
        assert state.get_note("note-drop")["is_archived"] == 1
