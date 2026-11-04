"""Тесты синхронизации источников CLI/MCP через один реестр (P1-01)."""

from __future__ import annotations

import sqlite3

import pytest

from kb_agent.sources.registry import SourceRegistry
from kb_agent.storage.state import StateManager


class TestSourceRegistrySqlite:
    """P1-01: SQLite — каноническое хранилище, YAML — только import/export."""

    def test_add_source_visible_in_sqlite_and_registry(self, state, config):
        """Добавленный через registry источник виден в SQLite."""
        registry = SourceRegistry(sources_path=config.sources_path, state=state)

        from kb_agent.sources.base import SourceConfig

        cfg = SourceConfig(
            id="yt_testchan",
            type="youtube",
            url="https://youtube.com/@testchan",
            enabled=True,
        )
        assert registry.add(cfg) is True

        # Проверяем в SQLite напрямую
        conn = state.connect()
        row = conn.execute(
            "SELECT source_id, source_type, channel, enabled FROM sources WHERE source_id = ?",
            ("yt_testchan",),
        ).fetchone()
        assert row is not None
        assert row["source_type"] == "youtube"
        assert row["channel"] == "https://youtube.com/@testchan"
        assert row["enabled"] == 1

    def test_remove_nonexistent_source_returns_false(self, state, config):
        """Удаление несуществующего источника возвращает False (P2-01)."""
        registry = SourceRegistry(sources_path=config.sources_path, state=state)
        assert registry.remove("nonexistent_id") is False

    def test_set_enabled_nonexistent_returns_false(self, state, config):
        """Переключение несуществующего источника возвращает False."""
        registry = SourceRegistry(sources_path=config.sources_path, state=state)
        assert registry.set_enabled("nonexistent_id", True) is False

    def test_list_uses_sqlite_not_yaml(self, state, config, temp_dir):
        """CLI и MCP видят один и тот же реестр (SQLite), а не YAML."""
        registry1 = SourceRegistry(sources_path=config.sources_path, state=state)
        registry2 = SourceRegistry(sources_path=config.sources_path, state=state)

        from kb_agent.sources.base import SourceConfig

        registry1.add(SourceConfig(id="tg_ch1", type="telegram", url="https://t.me/s/ch1", enabled=True))

        sources1 = registry1.list()
        sources2 = registry2.list()

        assert len(sources1) == 1
        assert len(sources2) == 1
        assert sources1[0].id == sources2[0].id == "tg_ch1"