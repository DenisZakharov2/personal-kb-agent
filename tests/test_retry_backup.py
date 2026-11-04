"""Волна 1: команды retry и backup (раздел 9 аудита)."""

from __future__ import annotations

import json
import zipfile

from typer.testing import CliRunner

from kb_agent.cli.main import app
import kb_agent.cli.commands.process as process_mod


def _insert_queue(
    state, external_id: str, queue_state: str = "failed", next_retry_at: str | None = None
) -> int:
    conn = state.connect()
    conn.execute(
        """
        INSERT INTO raw_queue
        (source_id, external_id, canonical_url, title, raw_text, raw_hash, state, last_error, next_retry_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("src", external_id, "https://x/1", "Title", "raw", "hash", queue_state, "err", next_retry_at),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM raw_queue WHERE external_id = ?", (external_id,)).fetchone()
    return row["id"]


class TestRetry:
    def test_retry_sets_state_new(self, config, state, monkeypatch):
        monkeypatch.setattr(process_mod, "load_config", lambda: config)
        qid = _insert_queue(state, "job-retry-1", queue_state="failed")

        runner = CliRunner()
        result = runner.invoke(app, ["retry", str(qid), "--json"], prog_name="kb-agent")
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["retried"] is True
        assert data["queue_id"] == qid
        assert data["state"] == "new"

        row = state.connect().execute("SELECT state FROM raw_queue WHERE id = ?", (qid,)).fetchone()
        assert row["state"] == "new"

    def test_retry_clears_lease_and_retry_at(self, config, state, monkeypatch):
        monkeypatch.setattr(process_mod, "load_config", lambda: config)
        conn = state.connect()
        conn.execute(
            """
            INSERT INTO raw_queue
            (source_id, external_id, canonical_url, title, raw_text, raw_hash, state,
             next_retry_at, lease_expires_at, processing_started_at)
            VALUES (?, ?, ?, ?, ?, ?, 'failed', ?, ?, ?)
            """,
            ("src", "job-retry-2", "https://x/2", "T", "r", "h", "2099-01-01", "2099-01-01", "2099-01-01"),
        )
        conn.commit()
        qid = conn.execute("SELECT id FROM raw_queue WHERE external_id = 'job-retry-2'").fetchone()["id"]

        runner = CliRunner()
        result = runner.invoke(app, ["retry", str(qid)], prog_name="kb-agent")
        assert result.exit_code == 0

        row = state.connect().execute(
            "SELECT state, next_retry_at, lease_expires_at, processing_started_at FROM raw_queue WHERE id = ?",
            (qid,),
        ).fetchone()
        assert row["state"] == "new"
        assert row["next_retry_at"] is None
        assert row["lease_expires_at"] is None
        assert row["processing_started_at"] is None

    def test_retry_unknown_id(self, config, state, monkeypatch):
        monkeypatch.setattr(process_mod, "load_config", lambda: config)
        runner = CliRunner()
        result = runner.invoke(app, ["retry", "999999"], prog_name="kb-agent")
        assert result.exit_code == 1


class TestBackup:
    def test_backup_creates_zip_with_db_and_vault(self, config, state, monkeypatch):
        monkeypatch.setattr(process_mod, "load_config", lambda: config)

        (config.vault_path / "Тема").mkdir(parents=True)
        (config.vault_path / "Тема" / "note.md").write_text("# Заметка\nТекст.", encoding="utf-8")

        runner = CliRunner()
        out_path = config.data_dir / "my_backup.zip"
        result = runner.invoke(
            app, ["backup", "--output", str(out_path), "--json"], prog_name="kb-agent"
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        archive = data["archive"]

        assert zipfile.is_zipfile(archive)
        with zipfile.ZipFile(archive) as zf:
            names = zf.namelist()
            assert "state.db" in names
            assert any(name.startswith("vault/") and name.endswith(".md") for name in names)

    def test_backup_default_output(self, config, state, monkeypatch):
        monkeypatch.setattr(process_mod, "load_config", lambda: config)
        runner = CliRunner()
        result = runner.invoke(app, ["backup"], prog_name="kb-agent")
        assert result.exit_code == 0, result.output
        assert "Резервная копия создана:" in result.output