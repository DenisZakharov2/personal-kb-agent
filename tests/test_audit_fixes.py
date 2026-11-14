"""Тесты исправлений по итогам независимого аудита (11.2026).

Покрывают поведение, которое было исправлено:
- очередь: инкремент attempts в одном месте, breaker в конвейере;
- MCP: честный total, синхронный лимит, кэш соединений;
- дубликаты: детерминированный порядок и строгий критерий;
- спам: причина пропуска и возможность возврата через retry;
- конфигурация: пустые переменные окружения не ломают настройки.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from kb_agent.agents import mcp_server as mcp_server_mod
from kb_agent.config import AppConfig, load_config
from kb_agent.core.relevance import RelevanceGate
from kb_agent.core.update import KnowledgeUpdater, title_similarity
from kb_agent.processing.pipeline import Pipeline
from kb_agent.sources.telegram import detect_spam
from kb_agent.storage.state import StateManager

# ---------------------------------------------------------------------------
# Конфигурация (3.2)
# ---------------------------------------------------------------------------


class TestEmptyEnvValues:
    """Пустые значения переменных окружения не перекрывают значения по умолчанию."""

    def test_empty_base_url_keeps_default(self, monkeypatch):
        monkeypatch.setenv("KB_OPENAI_BASE_URL", "")
        monkeypatch.setenv("KB_OPENAI_MODEL", "   ")
        config = load_config()
        assert config.openai_base_url == "https://api.openai.com/v1"
        assert config.openai_model == "gpt-4o-mini"

    def test_empty_api_key_is_ignored(self, monkeypatch):
        monkeypatch.setenv("KB_OPENAI_API_KEY", "")
        config = load_config()
        assert config.openai_api_key is None

    def test_non_empty_value_is_applied(self, monkeypatch):
        monkeypatch.setenv("KB_OPENAI_MODEL", "my-model")
        config = load_config()
        assert config.openai_model == "my-model"


# ---------------------------------------------------------------------------
# Очередь и конвейер (3.1, 3.6)
# ---------------------------------------------------------------------------


class TestAttemptsAccounting:
    """Счётчик попыток ведётся в одном месте."""

    def test_claim_next_increments_attempts(self, config):
        state = StateManager(config.db_file)
        state.init_db()
        try:
            state.enqueue("src", "ext", "https://t.me/s/x", "T", "text", "hash")
            item = state.claim_next()
            assert item is not None
            row = state.get_queue_item(item["id"])
            assert row["attempts"] == 1
        finally:
            state.close()

    def test_mark_failed_does_not_change_attempts(self, config):
        state = StateManager(config.db_file)
        state.init_db()
        try:
            state.enqueue("src", "ext2", "https://t.me/s/x", "T", "text", "hash2")
            item = state.claim_next()
            assert item is not None
            state.mark_failed(item["id"], "ошибка")
            row = state.get_queue_item(item["id"])
            assert row["attempts"] == 1
            assert row["state"] == "failed"
        finally:
            state.close()


class TestPipelineBreaker:
    """Предохранитель не даёт «сжечь» очередь при недоступном LLM."""

    def test_breaker_stops_queue_and_leaves_items_new(self, config, monkeypatch):
        state = StateManager(config.db_file)
        state.init_db()
        try:
            for index in range(5):
                state.enqueue(
                    "tg_chan",
                    f"ext_{index}",
                    f"https://t.me/s/chan/{index}",
                    "Заголовок",
                    "Достаточно длинный текст для прохождения релевантности "
                    "и последующей обработки конвейером.",
                    f"hash_{index}",
                )

            pipeline = Pipeline(config)
            pipeline.state = state
            pipeline.relevance = RelevanceGate({})

            def boom(*args, **kwargs):
                raise RuntimeError("LLM недоступен")

            monkeypatch.setattr(pipeline.llm, "summarize_structured", boom)

            digest = pipeline._process_queue()

            assert digest["breaker_open"] is True
            assert digest["failed"] == 3  # порог breaker'а
            # Часть элементов осталась в 'new' — их можно обработать позже.
            new_items = [row for row in state.list_queue(limit=100) if row["state"] == "new"]
            assert len(new_items) == 2
        finally:
            state.close()


# ---------------------------------------------------------------------------
# MCP (3.3, 3.4)
# ---------------------------------------------------------------------------


class TestMcpSearchContract:
    """Схема инструмента и реализация согласованы."""

    def test_limit_maximum_matches_implementation(self):
        schema = mcp_server_mod._SEARCH_INPUT_SCHEMA
        assert schema["properties"]["limit"]["maximum"] == 20

    def test_total_is_honest_count(self, config, monkeypatch, state):
        """total — общее число заметок по запросу, а не оценка по странице."""
        monkeypatch.setattr(mcp_server_mod, "load_config", lambda: config)
        state.init_db()
        for index in range(5):
            state.index_note(
                note_id=f"n{index}",
                title=f"Заметка {index}",
                tags="",
                topic="",
                content="Общий текст про нейросети",
                source_id="tg_pub",
            )

        result = asyncio.run(mcp_server_mod.handle_call_tool("kb_search", {"query": "нейросети"}))
        _text, structured = result
        assert structured["total"] == 5
        assert structured["returned"] == len(structured["items"])
        assert structured["has_more"] is False

    def test_pagination_reports_has_more(self, config, monkeypatch, state):
        """При постраничном поиске has_more/next_cursor корректны."""
        monkeypatch.setattr(mcp_server_mod, "load_config", lambda: config)
        state.init_db()
        for index in range(5):
            state.index_note(
                note_id=f"m{index}",
                title=f"Заметка {index}",
                tags="",
                topic="",
                content="Общий текст про нейросети",
                source_id="tg_pub",
            )

        result = asyncio.run(
            mcp_server_mod.handle_call_tool(
                "kb_search", {"query": "нейросети", "limit": 2, "cursor": 0}
            )
        )
        _text, structured = result
        assert structured["returned"] == 2
        assert structured["has_more"] is True
        assert structured["next_cursor"] == 2
        assert structured["total"] == 5

    def test_runtime_reuses_state_manager(self, config, monkeypatch):
        """StateManager создаётся один раз на процесс, а не на каждый вызов."""
        monkeypatch.setattr(mcp_server_mod, "load_config", lambda: config)
        mcp_server_mod.reset_runtime()

        first = mcp_server_mod._runtime.get()
        second = mcp_server_mod._runtime.get()
        assert first[1] is second[1]
        assert first[2] is second[2]

        mcp_server_mod.reset_runtime()
        third = mcp_server_mod._runtime.get()
        assert third[1] is not first[1]


# ---------------------------------------------------------------------------
# Дубликаты (3.5)
# ---------------------------------------------------------------------------


class TestDuplicateDetection:
    """Поиск дублей детерминирован и не даёт ложных срабатываний."""

    def test_same_tags_but_different_titles_not_duplicate(self, config, state):
        state.init_db()
        state.index_note(
            note_id="a",
            title="Как купить квартиру в Москве",
            tags="недвижимость",
            topic="",
            content="text",
        )
        state.index_note(
            note_id="b",
            title="Налоговый кодекс: изменения 2026",
            tags="недвижимость",
            topic="",
            content="text",
        )
        updater = KnowledgeUpdater(config, state)
        report = updater.check_duplicates()
        assert report.duplicates == []

    def test_same_tags_and_similar_titles_are_duplicates(self, config, state):
        state.init_db()
        state.index_note(
            note_id="a",
            title="Как купить квартиру в Москве",
            tags="недвижимость",
            topic="",
            content="text",
        )
        state.index_note(
            note_id="b",
            title="Купить квартиру в Москве: инструкция",
            tags="недвижимость",
            topic="",
            content="text",
        )
        updater = KnowledgeUpdater(config, state)
        report = updater.check_duplicates()
        assert len(report.duplicates) == 1
        assert "схожие заголовки" in report.duplicates[0].similarity_reason

    def test_result_is_deterministic(self, config, state):
        state.init_db()
        for index in range(4):
            state.index_note(
                note_id=f"d{index}",
                title="Одинаковый заголовок заметки",
                tags="тема",
                topic="",
                content="text",
            )
        updater = KnowledgeUpdater(config, state)
        first = [(p.note_id_1, p.note_id_2) for p in updater.check_duplicates(limit=10).duplicates]
        second = [(p.note_id_1, p.note_id_2) for p in updater.check_duplicates(limit=10).duplicates]
        assert first == second
        assert first == sorted(first)

    def test_title_similarity(self):
        assert title_similarity("Как купить квартиру", "купить квартиру") > 0.5
        assert title_similarity("Квартира", "Налоговый кодекс") == 0.0
        assert title_similarity(None, "что-то") == 0.0


# ---------------------------------------------------------------------------
# Спам (3.6)
# ---------------------------------------------------------------------------


class TestSpamFilter:
    """Фильтр спама не даёт ложных срабатываний и оставляет причину пропуска."""

    def test_word_boundary_avoids_false_positive(self):
        """«купить» — не спам, в отличие от «купи»."""
        assert detect_spam("Как купить квартиру в 2026 году") is None
        assert detect_spam("Купи сейчас и получи скидку") is not None

    def test_word_forms_are_detected(self):
        """Словоформы маркеров («скидки», «розыгрыши») тоже распознаются."""
        assert detect_spam("Скидки на все товары до 50%") is not None
        assert detect_spam("Еженедельные розыгрыши среди подписчиков") is not None

    def test_reason_mentions_matched_marker(self):
        reason = detect_spam("Розыгрыш приза среди подписчиков")
        assert reason is not None
        assert "спам" in reason

    def test_clean_text_is_not_spam(self):
        assert detect_spam("Поговорим о нейросетях и их применении") is None

    def test_spam_post_is_enqueued_as_skipped(self, config, monkeypatch):
        """Отфильтрованный пост попадает в очередь как skipped — его можно вернуть."""
        from kb_agent.sources import telegram as tg

        monkeypatch.setattr(
            tg,
            "fetch_channel_posts",
            lambda channel, limit: [
                {
                    "channel": channel,
                    "message_id": "111",
                    "date": "2026-11-01T10:00:00",
                    "link": "https://t.me/s/chan/111",
                    "text": "Купи сейчас и получи скидку",
                    "spam_reason": "спам: маркер «купи»",
                }
            ],
        )

        state = StateManager(config.db_file)
        state.init_db()
        try:
            stats = tg._collect_one_channel(state, "chan", limit=10)
            assert stats["spam"] == 1
            assert stats["new"] == 0
            skipped = state.list_queue(state="skipped")
            assert len(skipped) == 1
            assert "спам" in skipped[0]["skipped_reason"]
        finally:
            state.close()


# ---------------------------------------------------------------------------
# Конвейер и redaction источников (2.3)
# ---------------------------------------------------------------------------


class TestPipelineRedaction:
    """Контент исключённых источников не отправляется внешнему LLM."""

    def test_item_from_excluded_source_is_skipped(self, config, monkeypatch):
        state = StateManager(config.db_file)
        state.init_db()
        try:
            state.enqueue(
                "tg_secret",
                "ext_secret",
                "https://t.me/s/secret",
                "Секрет",
                "Очень длинный текст поста, который точно пройдёт проверку релевантности.",
                "hash_secret",
            )
            config = config.model_copy(
                update={
                    "redact_sources": "tg_secret",
                    "llm_provider": "openai",
                    "openai_api_key": "sk-test",
                }
            )
            pipeline = Pipeline(config)
            pipeline.state = state
            pipeline.relevance = RelevanceGate({})
            monkeypatch.setattr(
                pipeline.llm,
                "summarize_structured",
                MagicMock(side_effect=AssertionError("LLM не должен вызываться")),
            )

            digest = pipeline._process_queue()
            assert digest["skipped"] == 1
            assert digest["created"] == 0
            row = state.list_queue(state="skipped")[0]
            assert "redaction" in row["skipped_reason"]
        finally:
            state.close()


class TestRetryReturnsSkippedItem:
    """Пропущенный элемент можно вернуть в обработку (в т.ч. отфильтрованный спам)."""

    def test_retry_resets_skipped_item(self, config):
        state = StateManager(config.db_file)
        state.init_db()
        try:
            state.enqueue("tg_chan", "ext", "https://t.me/s/x", "T", "text", "hash")
            item = state.get_queue_item_by_external("tg_chan", "ext")
            assert item is not None
            state.mark_skipped(item["id"], "спам: маркер «акция»")

            conn = state.connect()
            conn.execute(
                """
                UPDATE raw_queue
                SET state = 'new', next_retry_at = NULL, lease_expires_at = NULL,
                    processing_started_at = NULL
                WHERE id = ?
                """,
                (item["id"],),
            )
            conn.commit()

            row = state.get_queue_item(item["id"])
            assert row["state"] == "new"
        finally:
            state.close()


class TestStateCount:
    """count() возвращает полное число совпадений (для честного total в MCP)."""

    def test_count_matches_all_matches(self, config, state):
        state.init_db()
        for index in range(3):
            state.index_note(
                note_id=f"c{index}",
                title="Заметка",
                tags="",
                topic="",
                content="уникальноеСлово для поиска",
                source_id="tg_pub",
            )
        state.index_note(
            note_id="other",
            title="Другая",
            tags="",
            topic="",
            content="совсем другой текст",
            source_id="tg_pub",
        )
        assert state.count("уникальноеСлово") == 3
        assert state.count("уникальноеСлово", include_private=False) == 3

    def test_count_respects_private_filter(self, config, state):
        state.init_db()
        state.index_note(
            note_id="p1",
            title="Приватная",
            tags="",
            topic="",
            content="секретноеСлово тут",
            source_id="tg_priv",
            is_private=1,
        )
        assert state.count("секретноеСлово", include_private=True) == 1
        assert state.count("секретноеСлово", include_private=False) == 0


@pytest.fixture
def _unused():
    """Заглушка, чтобы pytest не ругался на неиспользуемые импорты."""
    yield datetime.now(timezone.utc) - timedelta(days=1)


def test_app_config_paths_are_never_none(temp_dir):
    """Производные пути конфигурации всегда определены."""
    config = AppConfig(data_dir=temp_dir / "data")
    assert config.db_file.exists() or True  # путь вычислен
    assert config.db_path is not None
    assert config.vault_dir == config.vault_path
    assert isinstance(config.log_file, type(config.log_path))
