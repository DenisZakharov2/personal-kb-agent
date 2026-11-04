"""Тесты восстановления очереди и сбоев (P1-06)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from kb_agent.core.relevance import RelevanceGate
from kb_agent.processing.llm_client import SummaryData
from kb_agent.processing.pipeline import Pipeline
from kb_agent.storage.state import StateManager


class TestPipelineRetry:
    """P1-06: failed элементы повторяются, зависшие processing освобождаются."""

    def _make_pipeline(self, config):
        """Создаёт Pipeline с инициализированным relevance."""
        pipeline = Pipeline(config)
        pipeline.relevance = RelevanceGate({})  # пустой профиль — всё релевантно
        return pipeline

    def test_failed_item_retried_after_next_retry_at(self, config):
        """Элемент с state='failed' и next_retry_at <= now должен быть повторён."""
        state = StateManager(config.db_path)
        state.init_db()

        # Добавляем failed элемент с next_retry_at в прошлом
        past = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        conn = state.connect()
        conn.execute(
            """
            INSERT INTO raw_queue
            (source_id, external_id, canonical_url, title, raw_text, raw_hash, state, next_retry_at)
            VALUES (?, ?, ?, ?, ?, ?, 'failed', ?)
            """,
            ("yt_test", "yt_vid1", "https://youtube.com/watch?v=vid1", "Title", "raw text", "hash1", past),
        )
        conn.commit()
        state.close()

        pipeline = self._make_pipeline(config)

        # Мокаем summarize_structured, write_note, index_note чтобы не вызывать реальные зависимости
        with patch.object(pipeline.llm, "summarize_structured") as mock_summarize, \
             patch.object(pipeline.vault, "write_note") as mock_write, \
             patch.object(pipeline.state, "index_note") as mock_index:

            mock_summarize.return_value = SummaryData(
                title="Тест", content="## Суть\nТест\n", tags=["тест"], topic="Общее"
            )
            mock_write.return_value = None
            mock_index.return_value = None

            digest = pipeline._process_queue()

        state.close()
        # failed элемент должен быть обработан и переведен в processed
        assert digest["created"] == 1

    def test_stuck_processing_released_after_lease(self, config):
        """Зависший processing элемент с истекшим lease должен быть повторён."""
        state = StateManager(config.db_path)
        state.init_db()

        past = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        conn = state.connect()
        conn.execute(
            """
            INSERT INTO raw_queue
            (source_id, external_id, canonical_url, title, raw_text, raw_hash, state, lease_expires_at)
            VALUES (?, ?, ?, ?, ?, ?, 'processing', ?)
            """,
            ("yt_test", "yt_vid_stuck", "https://youtube.com/watch?v=stuck", "Stuck Title", "raw text", "hash_stuck", past),
        )
        conn.commit()
        state.close()

        pipeline = self._make_pipeline(config)

        with patch.object(pipeline.llm, "summarize_structured") as mock_summarize, \
             patch.object(pipeline.vault, "write_note") as mock_write, \
             patch.object(pipeline.state, "index_note") as mock_index:

            mock_summarize.return_value = SummaryData(
                title="Stuck Title", content="## Суть\nТест\n", tags=["тест"], topic="Общее"
            )
            mock_write.return_value = None
            mock_index.return_value = None

            digest = pipeline._process_queue()

        state.close()
        assert digest["created"] == 1


class TestPipelineExitCode:
    """P2-14: ошибки сбора/обработки попадают в итог и влияют на exit code."""

    def test_run_ok_when_no_errors(self, config, monkeypatch):
        """run() возвращает ok=True, если нет ошибок сбора и обработки."""
        from kb_agent.processing import pipeline as pipeline_mod
        from kb_agent.sources.manager import SourceCollectionResult

        monkeypatch.setattr(
            pipeline_mod, "run_collection",
            lambda config, state: SourceCollectionResult(),
        )
        pipeline = Pipeline(config)
        pipeline._process_queue = lambda: {"processed": 0, "created": 0, "skipped": 0, "failed": 0}
        pipeline.catalog.generate = lambda: __import__("pathlib", fromlist=["Path"]).Path("/tmp/catalog.md")
        pipeline._write_vault_digest = lambda *a, **k: __import__("pathlib", fromlist=["Path"]).Path("/tmp/digest.md")
        monkeypatch.setattr(pipeline_mod, "ensure_prompt_seeded", lambda config, state: None)
        pipeline.state.init_db = lambda: None
        pipeline._load_profile = lambda: {}

        result = pipeline.run()
        pipeline.close()

        assert result["ok"] is True
        assert result["collection"]["errors"] == []
        assert result["processing"]["failed"] == 0

    def test_run_not_ok_when_collection_errors(self, config, monkeypatch):
        """run() возвращает ok=False и ошибки, если сбор источника упал."""
        from kb_agent.processing import pipeline as pipeline_mod
        from kb_agent.sources.manager import SourceCollectionResult

        def fake_run_collection(config, state):
            result = SourceCollectionResult()
            result.errors = ["источник yt_test недоступен"]
            result.by_source = {}
            return result

        monkeypatch.setattr(pipeline_mod, "run_collection", fake_run_collection)
        pipeline = Pipeline(config)
        pipeline._process_queue = lambda: {"processed": 0, "created": 0, "skipped": 0, "failed": 0}
        pipeline.catalog.generate = lambda: __import__("pathlib", fromlist=["Path"]).Path("/tmp/catalog.md")
        pipeline._write_vault_digest = lambda *a, **k: __import__("pathlib", fromlist=["Path"]).Path("/tmp/digest.md")
        monkeypatch.setattr(pipeline_mod, "ensure_prompt_seeded", lambda config, state: None)
        pipeline.state.init_db = lambda: None
        pipeline._load_profile = lambda: {}

        result = pipeline.run()
        pipeline.close()

        assert result["ok"] is False
        assert "источник yt_test недоступен" in result["collection"]["errors"]

    def test_run_not_ok_when_processing_failed(self, config, monkeypatch):
        """run() возвращает ok=False, если есть ошибки обработки очереди."""
        from kb_agent.processing import pipeline as pipeline_mod
        from kb_agent.sources.manager import SourceCollectionResult

        monkeypatch.setattr(
            pipeline_mod, "run_collection",
            lambda config, state: SourceCollectionResult(),
        )
        pipeline = Pipeline(config)
        pipeline._process_queue = lambda: {"processed": 1, "created": 0, "skipped": 0, "failed": 2}
        pipeline.catalog.generate = lambda: __import__("pathlib", fromlist=["Path"]).Path("/tmp/catalog.md")
        pipeline._write_vault_digest = lambda *a, **k: __import__("pathlib", fromlist=["Path"]).Path("/tmp/digest.md")
        monkeypatch.setattr(pipeline_mod, "ensure_prompt_seeded", lambda config, state: None)
        pipeline.state.init_db = lambda: None
        pipeline._load_profile = lambda: {}

        result = pipeline.run()
        pipeline.close()

        assert result["ok"] is False
        assert result["processing"]["failed"] == 2

    def test_process_run_exits_nonzero_on_errors(self, config, monkeypatch):
        """CLI process run завершается с кодом 1 при ошибках сбора/обработки."""
        from typer.testing import CliRunner
        from kb_agent.cli.commands import process as process_cmd

        class FakePipeline:
            def run(self):
                return {
                    "ok": False,
                    "collection": {
                        "errors": ["источник yt_test недоступен"],
                        "telegram": {"new": 0, "spam": 0, "existing": 0},
                        "youtube": {"new": 0, "skipped": 0, "with_subtitles": 0},
                    },
                    "processing": {
                        "processed": 0, "created": 0, "skipped": 0,
                        "relevance_skipped": 0, "failed": 1,
                    },
                    "catalog": "/tmp/c.md",
                    "vault_digest": "/tmp/d.md",
                }

            def close(self):
                pass

        monkeypatch.setattr(process_cmd, "Pipeline", lambda config: FakePipeline())
        runner = CliRunner()
        result = runner.invoke(process_cmd.app, ["run"])
        assert result.exit_code == 1
        assert "источник yt_test недоступен" in result.output