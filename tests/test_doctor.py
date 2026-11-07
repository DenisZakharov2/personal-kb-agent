"""Волна 1: kb-agent doctor — проверка целостности (раздел 9 аудита)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import yaml
from typer.testing import CliRunner

import kb_agent.cli.commands.process as process_mod
from kb_agent.cli.main import app


def _run_doctor(config, monkeypatch, json_output=True):
    monkeypatch.setattr(process_mod, "load_config", lambda: config)
    runner = CliRunner()
    args = ["doctor", "--json"] if json_output else ["doctor"]
    return runner.invoke(app, args, prog_name="kb-agent")


class TestDoctor:
    def test_doctor_clean_on_empty_db(self, config, state, monkeypatch):
        result = _run_doctor(config, monkeypatch)
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["count"] == 0
        assert data["issues"] == []

    def test_detects_orphan_file_and_fts_without_file(self, config, state, monkeypatch):
        """Орфан-файл в vault и FTS-запись без файла обнаруживаются."""
        vault = config.vault_path
        (vault / "Тема").mkdir(parents=True)
        (vault / "Тема" / "2026-01-01_Орфан.md").write_text(
            "---\nnote_id: orphan-file-1\ntitle: Орфан\n---\n\nКонтент орфана.\n",
            encoding="utf-8",
        )

        conn = state.connect()
        conn.execute(
            "INSERT INTO notes_fts (note_id, title, tags, topic, content) VALUES (?, ?, ?, ?, ?)",
            ("fts-ghost", "Призрак", "", "", "призрачный контент"),
        )
        conn.commit()

        result = _run_doctor(config, monkeypatch)
        assert result.exit_code == 0
        data = json.loads(result.output)
        kinds = {issue["kind"] for issue in data["issues"]}
        assert "orphan_file" in kinds, data
        assert "fts_without_file" in kinds, data
        ghost = next(i for i in data["issues"] if i["kind"] == "fts_without_file")
        assert ghost["note_id"] == "fts-ghost"

    def test_detects_stuck_processing_and_failed_without_retry(self, config, state, monkeypatch):
        """Зависшие processing и failed без next_retry_at находятся."""
        datetime.now(timezone.utc).isoformat()
        past = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        conn = state.connect()
        conn.execute(
            """
            INSERT INTO raw_queue
            (source_id, external_id, canonical_url, title, raw_text, raw_hash, state, lease_expires_at)
            VALUES (?, ?, ?, ?, ?, ?, 'processing', ?)
            """,
            ("src", "e1", "https://x/1", "T", "r", "h1", past),
        )
        conn.execute(
            """
            INSERT INTO raw_queue
            (source_id, external_id, canonical_url, title, raw_text, raw_hash, state, last_error, next_retry_at)
            VALUES (?, ?, ?, ?, ?, ?, 'failed', ?, NULL)
            """,
            ("src", "e2", "https://x/2", "T", "r", "h2", "ошибка"),
        )
        conn.commit()

        result = _run_doctor(config, monkeypatch)
        data = json.loads(result.output)
        kinds = {issue["kind"] for issue in data["issues"]}
        assert "stuck_processing" in kinds, data
        assert "failed_without_retry" in kinds, data

    def test_detects_source_mismatch_sqlite_vs_yaml(self, config, state, monkeypatch):
        """Расхождение источников между SQLite и sources.yaml фиксируется."""
        conn = state.connect()
        conn.execute(
            "INSERT INTO sources (source_id, source_type, channel, enabled) VALUES ('tg_extra','telegram','extra',1)"
        )
        conn.commit()

        config.sources_path.parent.mkdir(parents=True, exist_ok=True)
        config.sources_path.write_text(
            yaml.dump(
                {
                    "version": 1,
                    "sources": [
                        {
                            "id": "tg_yaml_only",
                            "type": "telegram",
                            "channel": "chan",
                            "enabled": True,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        result = _run_doctor(config, monkeypatch)
        data = json.loads(result.output)
        kinds = {issue["kind"] for issue in data["issues"]}
        assert "source_mismatch" in kinds, data
        directions = {i["detail"] for i in data["issues"] if i["kind"] == "source_mismatch"}
        assert any("SQLite" in d and "sources.yaml" in d for d in directions)
        assert any("sources.yaml" in d and "SQLite" in d for d in directions)

    def test_doctor_includes_sources_health(self, config, state, monkeypatch):
        """doctor выводит состояние источников (sources) в JSON и тексте."""
        conn = state.connect()
        conn.execute(
            "INSERT INTO sources (source_id, source_type, channel, enabled) "
            "VALUES ('tg_rubiconzone','telegram','https://t.me/s/rubiconzone',1)"
        )
        conn.execute(
            "INSERT INTO sources (source_id, source_type, channel, enabled) "
            "VALUES ('yt_amorev','youtube','https://youtube.com/@amorev',0)"
        )
        conn.commit()

        # JSON-вывод содержит секцию sources
        result = _run_doctor(config, monkeypatch)
        assert result.exit_code == 0
        data = json.loads(result.output)

        assert "sources" in data, data
        sources = {s["source_id"]: s for s in data["sources"]}
        assert "tg_rubiconzone" in sources
        assert sources["tg_rubiconzone"]["source_type"] == "telegram"
        assert sources["tg_rubiconzone"]["url"] == "https://t.me/s/rubiconzone"
        assert sources["tg_rubiconzone"]["enabled"] is True

        assert "yt_amorev" in sources
        assert sources["yt_amorev"]["enabled"] is False

        # Текстовый вывод тоже содержит источники и их статусы
        result_text = _run_doctor(config, monkeypatch, json_output=False)
        assert result_text.exit_code == 0
        assert "tg_rubiconzone" in result_text.output
        assert "yt_amorev" in result_text.output
        assert "включён" in result_text.output
        assert "выключен" in result_text.output

    def test_doctor_sources_empty_when_no_sources(self, config, state, monkeypatch):
        """Без источников doctor возвращает пустой список sources."""
        result = _run_doctor(config, monkeypatch)
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["sources"] == []
