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