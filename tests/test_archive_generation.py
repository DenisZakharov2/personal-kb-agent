"""Тесты архивации заметок и génération_log (раздел 9 аудита)."""

from __future__ import annotations

from kb_agent.processing.pipeline import Pipeline
from kb_agent.processing.llm_client import SummaryData
from kb_agent.storage.state import StateManager


class TestArchiveRestore:
    def test_set_note_archived_and_search_excludes(self, config, state):
        state.index_note(
            note_id="n-archive-1",
            title="Архивируемая",
            tags="тэг",
            topic="Тема",
            content="контент для архива",
            source_id="tg_test",
            external_id="ext_arc",
        )
        assert state.set_note_archived("n-archive-1", True) is True
        note = state.get_note("n-archive-1")
        assert note["is_archived"] == 1

        # По умолчанию архивные исключаются из поиска
        found = state.search("архива", limit=5)
        assert len(found) == 0

        found_all = state.search("архива", limit=5, include_archived=True)
        assert len(found_all) == 1

        assert state.set_note_archived("n-archive-1", False) is True
        found = state.search("архива", limit=5)
        assert len(found) == 1

    def test_vault_archive_and_restore(self, config, state):
        from kb_agent.storage.vault import VaultManager

        vault = VaultManager(config)
        path = vault.write_note(
            note_id="n-vault-arc",
            source_id="tg_test",
            source_type="telegram",
            external_id="ext_vault",
            canonical_url="https://t.me/c/1",
            published_at=None,
            received_at="2026-01-01T10:00:00+00:00",
            title="Для архива",
            tags="тэг",
            topic="Тема",
            content="содержимое",
        )
        archived = vault.archive_note("n-vault-arc")
        assert archived is not None
        assert archived.parent.name == "Архив"

        # Повторный archive — безопасно (идемпотентно, файл уже в архиве)
        archived_again = vault.archive_note("n-vault-arc")
        assert archived_again == archived
        assert archived_again.parent.name == "Архив"

        restored = vault.restore_note("n-vault-arc", topic="Тема")
        assert restored is not None
        assert restored.parent.name != "Архив"


class TestGenerationLog:
    def test_save_and_count_generation_log(self, config, state):
        state.index_note(
            note_id="n-gen-1",
            title="Т",
            tags="тэг",
            topic="Тема",
            content="контент",
        )
        row_id = state.save_generation_log(
            note_id="n-gen-1",
            operation="summarize",
            model="test-model",
            prompt_name="summarize_prompt",
            prompt_version=1,
            prompt_tokens=10,
            completion_tokens=20,
            latency_ms=123.5,
            cost_usd=0.001,
        )
        assert row_id is not None
        conn = state.connect()
        rows = conn.execute("SELECT COUNT(*) AS c FROM generation_log").fetchone()
        assert rows["c"] == 1
        row = conn.execute(
            "SELECT operation, model, prompt_tokens, completion_tokens FROM generation_log WHERE id = ?",
            (row_id,),
        ).fetchone()
        assert row["operation"] == "summarize"
        assert row["model"] == "test-model"
        assert row["prompt_tokens"] == 10
        assert row["completion_tokens"] == 20

    def test_generation_log_invalid_operation_rejected(self, state):
        try:
            state.save_generation_log(
                note_id=None, operation="bad_op", model="m"
            )
            raise AssertionError("должно быть ValueError")
        except ValueError:
            pass


class TestPipelineIdempotentUpdate:
    def test_pipeline_updates_existing_note_instead_of_duplicate(self, config, monkeypatch):
        state = StateManager(config.db_path)
        state.init_db()
        # Предварительно созданная заметка с тем же external_id
        state.index_note(
            note_id="existing-note",
            title="Старое",
            tags="тэг",
            topic="Тема",
            content="старое содержимое",
            source_id="tg_test",
            external_id="ext_pipeline",
            canonical_url="https://t.me/c/1",
            revision=1,
        )
        state.close()

        pipeline = Pipeline(config)
        pipeline.relevance = __import__("kb_agent.core.relevance", fromlist=["RelevanceGate"]).RelevanceGate({})

        with __import__("unittest.mock", fromlist=["patch"]).patch.object(
            pipeline.llm, "summarize_structured"
        ) as mock_summarize, \
             __import__("unittest.mock", fromlist=["patch"]).patch.object(
                 pipeline.vault, "write_note"
             ) as mock_write, \
             __import__("unittest.mock", fromlist=["patch"]).patch.object(
                 pipeline.state, "index_note"
             ) as mock_index:

            mock_summarize.return_value = SummaryData(
                title="Обновлённое", content="## Суть\nновое содержимое\n",
                tags=["тэг"], topic="Тема",
            )
            mock_write.return_value = None
            mock_index.return_value = None

            conn = state.connect()
            conn.execute(
                """
                INSERT INTO raw_queue
                (source_id, external_id, canonical_url, title, raw_text, raw_hash, state)
                VALUES (?, ?, ?, ?, ?, ?, 'new')
                """,
                ("tg_test", "ext_pipeline", "https://t.me/c/1", "Заголовок", "сырой текст", "hash_pipe"),
            )
            conn.commit()
            state.close()

            digest = pipeline._process_queue()
            assert digest["updated"] == 1
            assert digest["created"] == 0
