"""Тесты инсайтов и связей между заметками (R-02 аудита).

Проверяют:
- _link_related_notes создаёт связи в note_links при совпадении тегов
- get_insights возвращает инсайты повторяющихся тем (_find_repeating_themes)
- get_insights возвращает инсайты существующих связей (_find_existing_links)
"""

from __future__ import annotations

from kb_agent.core.insights import InsightsEngine
from kb_agent.processing.pipeline import Pipeline


class TestLinkRelatedNotes:
    """Связи между заметками по общим тегам."""

    def test_link_related_notes_creates_links(self, config, state):
        """При совпадении тегов создаются ссылки references в note_links."""
        # Индексируем две заметки с общим тегом
        state.index_note(
            note_id="note-a",
            title="Заметка A",
            tags="python,ии",
            topic="Программирование",
            content="контент А",
            source_id="tg_test",
            external_id="ext-a",
            canonical_url="",
            file_path="",
            content_hash="hash-a",
            revision=1,
        )
        state.index_note(
            note_id="note-b",
            title="Заметка B",
            tags="python, automation",
            topic="Программирование",
            content="контент Б",
            source_id="tg_test",
            external_id="ext-b",
            canonical_url="",
            file_path="",
            content_hash="hash-b",
            revision=1,
        )

        pipeline = Pipeline(config)
        pipeline._link_related_notes("note-a", ["python", "ии"])

        # Связь должна быть создана (в обе стороны через INSERT OR IGNORE)
        conn = state.connect()
        rows = conn.execute(
            "SELECT source_note_id, target_note_id, link_type FROM note_links"
        ).fetchall()
        link_types = {(r["source_note_id"], r["target_note_id"], r["link_type"]) for r in rows}
        assert ("note-a", "note-b", "references") in link_types, rows

    def test_link_related_notes_no_common_tags(self, config, state):
        """Без общих тегов связи не создаются."""
        state.index_note(
            note_id="note-a",
            title="Заметка A",
            tags="python",
            topic="Программирование",
            content="контент А",
            source_id="tg_test",
            external_id="ext-a",
            canonical_url="",
            file_path="",
            content_hash="hash-a",
            revision=1,
        )
        state.index_note(
            note_id="note-b",
            title="Заметка B",
            tags="yaml,конфиг",
            topic="Настройка",
            content="контент Б",
            source_id="tg_test",
            external_id="ext-b",
            canonical_url="",
            file_path="",
            content_hash="hash-b",
            revision=1,
        )

        pipeline = Pipeline(config)
        pipeline._link_related_notes("note-a", ["python"])

        conn = state.connect()
        rows = conn.execute("SELECT COUNT(*) AS c FROM note_links").fetchone()["c"]
        assert rows == 0

    def test_link_related_notes_empty_tags(self, config, state):
        """Пустые теги — не создаём связей, не падаем."""
        pipeline = Pipeline(config)
        pipeline._link_related_notes("note-a", [])
        pipeline._link_related_notes("note-a", ["", "  ", None])


class TestInsightsEngine:
    """Инсайты базы знаний."""

    def test_get_insights_repeating_themes(self, config, state):
        """Повторяющиеся теги дают инсайты repeating_theme."""
        state.index_note(
            note_id="note-a",
            title="Заметка A",
            tags="python,ии",
            topic="Программирование",
            content="контент А",
            source_id="tg_test",
            external_id="ext-a",
            canonical_url="",
            file_path="",
            content_hash="hash-a",
            revision=1,
        )
        state.index_note(
            note_id="note-b",
            title="Заметка B",
            tags="python,автоматизация",
            topic="Программирование",
            content="контент Б",
            source_id="tg_test",
            external_id="ext-b",
            canonical_url="",
            file_path="",
            content_hash="hash-b",
            revision=1,
        )

        engine = InsightsEngine(config, state)
        response = engine.get_insights()

        themes = [i for i in response.insights if i.link_type == "repeating_theme"]
        assert len(themes) >= 1, (
            f"Ожидался инсайт repeating_theme, got: {[i.statement for i in response.insights]}"
        )
        assert any("python" in i.statement for i in themes), themes

    def test_get_insights_existing_links(self, config, state):
        """Существующие ссылки в note_links дают инсайты connection."""
        state.add_link("note-a", "note-b", "references")
        state.add_link("note-b", "note-c", "contradicts")

        engine = InsightsEngine(config, state)
        response = engine.get_insights()

        links = [i for i in response.insights if i.link_type == "connection"]
        assert len(links) == 2, (
            f"Ожидалось 2 connection-инсайта, got: {[i.statement for i in response.insights]}"
        )

    def test_get_insights_empty_db(self, config, state):
        """На пустой базе инсайтов нет, не падает."""
        engine = InsightsEngine(config, state)
        response = engine.get_insights()
        assert response.insights == []

    def test_get_insights_limit(self, config, state):
        """Параметр limit ограничивает количество инсайтов."""
        # Создаём много заметок с общим тегом
        for i in range(10):
            state.index_note(
                note_id=f"note-{i}",
                title=f"Заметка {i}",
                tags="общий,тег",
                topic="Т",
                content=f"контент {i}",
                source_id="tg_test",
                external_id=f"ext-{i}",
                canonical_url="",
                file_path="",
                content_hash=f"hash-{i}",
                revision=1,
            )

        engine = InsightsEngine(config, state)
        response = engine.get_insights(limit=3)
        assert len(response.insights) <= 3
