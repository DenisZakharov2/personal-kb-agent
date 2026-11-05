"""Тесты идемпотентной очереди с lease, retry и reconciler (Волна 1).

Проверяется, что повторная обработка элемента очереди не создаёт дублей
файлов в vault, а также работает команда `kb-agent process reconcile`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kb_agent.cli.commands.process import app as process_app
from kb_agent.core.relevance import RelevanceGate
from kb_agent.processing.pipeline import Pipeline
from kb_agent.processing.llm_client import SummaryData
from kb_agent.storage.state import StateManager
from typer.testing import CliRunner


class TestQueueNoteId:
    """note_id сохраняется в raw_queue и повторно используется при повторе."""

    def test_set_and_get_queue_item_note_id(self, config, state):
        """set_queue_item_note_id / get_queue_item работают корректно."""
        conn = state.connect()
        conn.execute(
            """
            INSERT INTO raw_queue
            (source_id, external_id, canonical_url, title, raw_text, raw_hash, state)
            VALUES (?, ?, ?, ?, ?, ?, 'new')
            """,
            ("src", "ext1", "https://example.com/1", "T", "text", "hash1"),
        )
        qid = conn.execute(
            "SELECT id FROM raw_queue WHERE external_id = ?", ("ext1",)
        ).fetchone()["id"]
        conn.commit()

        state.set_queue_item_note_id(qid, "note_abc123")
        item = state.get_queue_item(qid)
        assert item is not None
        assert item["note_id"] == "note_abc123"

    def test_get_queue_item_by_external(self, config, state):
        """get_queue_item_by_external находит элемент по source_id/external_id."""
        state.enqueue(
            source_id="tg_test",
            external_id="tg_123",
            canonical_url="https://t.me/s/chan/123",
            title="Title",
            raw_text="text",
            raw_hash="hash123",
        )
        item = state.get_queue_item_by_external("tg_test", "tg_123")
        assert item is not None
        assert item["external_id"] == "tg_123"
        assert item["source_id"] == "tg_test"

    def test_get_queue_items_without_note_id(self, config, state):
        """get_queue_items_without_note_id находит обработанные без note_id."""
        # processed с note_id
        state.enqueue(
            source_id="tg_test", external_id="tg_1", canonical_url="u",
            title="T1", raw_text="t1", raw_hash="h1",
        )
        state.enqueue(
            source_id="tg_test", external_id="tg_2", canonical_url="u",
            title="T2", raw_text="t2", raw_hash="h2",
        )
        # Помечаем оба processed, но только одному даём note_id
        conn = state.connect()
        row1 = conn.execute(
            "SELECT id FROM raw_queue WHERE external_id = 'tg_1'"
        ).fetchone()
        row2 = conn.execute(
            "SELECT id FROM raw_queue WHERE external_id = 'tg_2'"
        ).fetchone()
        conn.execute(
            "UPDATE raw_queue SET state = 'processed', note_id = 'note_1' WHERE id = ?",
            (row1["id"],),
        )
        conn.execute(
            "UPDATE raw_queue SET state = 'processed' WHERE id = ?",
            (row2["id"],),
        )
        conn.commit()

        without = state.get_queue_items_without_note_id()
        assert len(without) == 1
        assert without[0]["external_id"] == "tg_2"


class TestPipelineIdempotentRetry:
    """Повтор обработки failed-элемента использует тот же note_id (без дублей)."""

    def _make_pipeline(self, config):
        pipeline = Pipeline(config)
        pipeline.relevance = RelevanceGate({})  # пустой профиль — всё релевантно
        return pipeline

    def test_retry_uses_stored_note_id_no_duplicate(
        self, config, state, temp_dir, monkeypatch
    ):
        """При повторе failed-элемента используется сохранённый note_id."""
        # Вставляем элемент в очередь с note_id, но без записи в notes
        state.init_db()
        conn = state.connect()
        conn.execute(
            """
            INSERT INTO raw_queue
            (source_id, external_id, canonical_url, title, raw_text, raw_hash,
             state, note_id, next_retry_at)
            VALUES (?, ?, ?, ?, ?, ?, 'failed', 'note_reuse_123', ?)
            """,
            (
                "yt_test", "yt_vid_reuse", "https://youtube.com/watch?v=reuse",
                "Title", "raw text", "hash_reuse",
                (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
            ),
        )
        qid = conn.execute(
            "SELECT id FROM raw_queue WHERE external_id = ?", ("yt_vid_reuse",)
        ).fetchone()["id"]
        conn.commit()
        state.close()

        pipeline = self._make_pipeline(config)

        with patch.object(pipeline.llm, "summarize_structured") as mock_summarize, \
             patch.object(pipeline.vault, "write_note") as mock_write, \
             patch.object(pipeline.state, "index_note") as mock_index, \
             patch.object(pipeline.state, "get_note_by_external", return_value=None):

            mock_summarize.return_value = SummaryData(
                title="Реузи", content="## Суть\nТест\n", tags=["тест"], topic="Общее"
            )
            mock_write.return_value = None
            mock_index.return_value = None

            digest = pipeline._process_queue()

        # Должен быть создан 1 заметка (не 2), и write_note вызван с сохранённым note_id
        assert digest["created"] == 1
        mock_write.assert_called_once()
        call_kwargs = mock_write.call_args.kwargs
        assert call_kwargs["note_id"] == "note_reuse_123"


class TestReconcileCommand:
    """Тесты команды `kb-agent process reconcile`."""

    def test_reconcile_finds_orphan_vault_file(self, config, state, temp_dir, monkeypatch):
        """reconcile находит файл в vault без записи в notes."""
        # Создаём файл в vault
        vault_dir = config.vault_path / "Тест"
        vault_dir.mkdir(parents=True, exist_ok=True)
        note_file = vault_dir / "2026-11-05T10_orphan.md"
        note_file.write_text(
            "---\nnote_id: note_orphan\ntitle: Orphan\ntopic: Тест\ntags: []\n---\n\nСодержимое сироты.",
            encoding="utf-8",
        )

        # Добавляем элемент в raw_queue с этим note_id (failed, без next_retry_at)
        state.init_db()
        conn = state.connect()
        conn.execute(
            """
            INSERT INTO raw_queue
            (source_id, external_id, canonical_url, title, raw_text, raw_hash,
             state, note_id, next_retry_at)
            VALUES (?, ?, ?, ?, ?, ?, 'failed', 'note_orphan', NULL)
            """,
            ("src", "ext_orphan", "https://example.com/orphan", "Orphan", "text", "hash_orphan"),
        )
        conn.commit()
        state.close()

        runner = CliRunner()
        monkeypatch.setattr(
            "kb_agent.cli.commands.process.load_config", lambda: config
        )
        result = runner.invoke(process_app, ["reconcile", "--json"], prog_name="kb-agent")
        assert result.exit_code == 0, result.output
        import json
        data = json.loads(result.output)
        assert data["count"] >= 1
        kinds = {i["kind"] for i in data["issues"]}
        assert "orphan_vault_file" in kinds

    def test_reconcile_fix_indexes_orphan(self, config, state, temp_dir, monkeypatch):
        """reconcile --fix индексирует файл-орфан и помечает очередь processed."""
        vault_dir = config.vault_path / "Тест"
        vault_dir.mkdir(parents=True, exist_ok=True)
        note_file = vault_dir / "2026-11-05T10_fixme.md"
        note_file.write_text(
            "---\nnote_id: note_fixme\ntitle: FixMe\ntopic: Тест\ntags: []\n---\n\nСодержимое для исправления.",
            encoding="utf-8",
        )

        state.init_db()
        conn = state.connect()
        conn.execute(
            """
            INSERT INTO raw_queue
            (source_id, external_id, canonical_url, title, raw_text, raw_hash,
             state, note_id, next_retry_at)
            VALUES (?, ?, ?, ?, ?, ?, 'failed', 'note_fixme', NULL)
            """,
            ("src", "ext_fixme", "https://example.com/fixme", "FixMe", "text", "hash_fixme"),
        )
        qid = conn.execute(
            "SELECT id FROM raw_queue WHERE external_id = ?", ("ext_fixme",)
        ).fetchone()["id"]
        conn.commit()
        state.close()

        runner = CliRunner()
        monkeypatch.setattr(
            "kb_agent.cli.commands.process.load_config", lambda: config
        )
        result = runner.invoke(
            process_app, ["reconcile", "--fix", "--json"], prog_name="kb-agent"
        )
        assert result.exit_code == 0, result.output
        import json
        data = json.loads(result.output)
        # Должен быть исправлен хотя бы один орфан
        assert data["fixed_count"] >= 1
        fixed_kinds = {f["kind"] for f in data["fixed"]}
        assert "orphan_indexed" in fixed_kinds

        # Проверяем, что очередь помечена processed
        state2 = StateManager(config.db_path)
        state2.init_db()
        row = state2.connect().execute(
            "SELECT state FROM raw_queue WHERE id = ?", (qid,)
        ).fetchone()
        assert row["state"] == "processed"
        state2.close()
