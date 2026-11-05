"""Тесты синхронизации источников CLI/MCP через один реестр (P1-01)."""

from __future__ import annotations

from kb_agent.sources.base import SourceConfig
from kb_agent.sources.manager import SourceManager
from kb_agent.sources.registry import SourceRegistry
from kb_agent.storage.pending import PendingManager


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

        registry1.add(SourceConfig(id="tg_ch123", type="telegram", url="https://t.me/s/ch123", enabled=True))

        sources1 = registry1.list()
        sources2 = registry2.list()

        assert len(sources1) == 1
        assert len(sources2) == 1
        assert sources1[0].id == sources2[0].id == "tg_ch123"


class TestPendingAffectsCollector:
    """P1-01: pending add_source/remove_source должны реально влиять на сбор.

    Потенциальный сборщик читает источники из SourceRegistry → SQLite,
    поэтому после confirm_change(add_source) источник должен появиться в
    реестре, а после confirm_change(remove_source) — исчезнуть.
    """

    def test_pending_add_source_visible_in_collector(self, state, config):
        """После подтверждения add_source источник виден в реестре сборщика."""
        pm = PendingManager(state)
        change_id = pm.create_change(
            action="add_source",
            entity_type="source",
            entity_id="yt_newchan",
            parameters={
                "source_id": "yt_newchan",
                "source_type": "youtube",
                "channel": "https://youtube.com/@newchan",
                "enabled": True,
            },
        )
        assert pm.confirm_change(change_id, revision=0) is True

        manager = SourceManager(config, state)
        ids = {s.id for s in manager.registry.list()}
        assert "yt_newchan" in ids, f"Источник не попал в реестр сборщика: {ids}"

    def test_pending_remove_source_not_in_collector(self, state, config):
        """После подтверждения remove_source источника нет в реестре сборщика."""
        registry = SourceRegistry(sources_path=config.sources_path, state=state)
        registry.add(
            SourceConfig(id="yt_gone", type="youtube", url="https://youtube.com/@gone", enabled=True)
        )

        pm = PendingManager(state)
        change_id = pm.create_change(
            action="remove_source",
            entity_type="source",
            entity_id="yt_gone",
            parameters={"source_id": "yt_gone"},
        )
        assert pm.confirm_change(change_id, revision=0) is True

        manager = SourceManager(config, state)
        ids = {s.id for s in manager.registry.list()}
        assert "yt_gone" not in ids, f"Источник всё ещё в реестре сборщика: {ids}"


class TestCollectorReadsFromSqliteNotYaml:
    """P1-01: SourceManager читает источники из SQLite (канонического хранилища),
    а не из sources.yaml. YAML — только экспорт/импорт."""

    def test_source_in_yaml_only_not_visible_to_collector(self, state, config, temp_dir):
        """Источник, записанный только в sources.yaml (не в SQLite),
        не должен быть виден сборщику."""
        # Пишем источник напрямую в YAML, минуя SQLite
        import yaml
        yaml_data = {
            "version": 1,
            "sources": [
                {
                    "id": "yt_yaml_only",
                    "type": "youtube",
                    "url": "https://youtube.com/@yamlonly",
                    "enabled": True,
                }
            ],
        }
        config.sources_path.write_text(
            yaml.dump(yaml_data, allow_unicode=True), encoding="utf-8"
        )

        manager = SourceManager(config, state)
        ids = {s.id for s in manager.registry.list()}
        assert "yt_yaml_only" not in ids, (
            f"Источник из YAML не должен попасть в сборщик (только SQLite): {ids}"
        )

    def test_source_in_sqlite_only_visible_to_collector(self, state, config, temp_dir):
        """Источник, записанный только в SQLite (не в YAML),
        должен быть виден сборщику."""
        # Убеждаемся, что YAML не содержит источников (или файла нет)
        if config.sources_path.exists():
            config.sources_path.unlink()

        registry = SourceRegistry(sources_path=config.sources_path, state=state)
        registry.add(
            SourceConfig(
                id="tg_sqlite_only",
                type="telegram",
                url="https://t.me/s/sqliteonly",
                enabled=True,
            )
        )

        manager = SourceManager(config, state)
        ids = {s.id for s in manager.registry.list()}
        assert "tg_sqlite_only" in ids, (
            f"Источник из SQLite должен быть виден сборщику: {ids}"
        )

    def test_manager_without_yaml_file_still_works(self, state, config, temp_dir):
        """SourceManager работает даже если sources.yaml отсутствует entirely."""
        if config.sources_path.exists():
            config.sources_path.unlink()

        manager = SourceManager(config, state)
        sources = manager.registry.list()
        assert sources == []

        # Можно добавить источник через тот же реестр менеджера
        manager.registry.add(
            SourceConfig(
                id="tg_no_yaml",
                type="telegram",
                url="https://t.me/s/noyaml",
                enabled=True,
            )
        )

        sources = manager.registry.list()
        assert len(sources) == 1
        assert sources[0].id == "tg_no_yaml"