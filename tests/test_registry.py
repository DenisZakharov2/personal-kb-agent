"""P2-02: атомарная запись sources.yaml в SourceRegistry."""

from __future__ import annotations

import os
import re

import pytest
import yaml

from kb_agent.sources.registry import SourceRegistry
from kb_agent.sources.base import SourceConfig


def _write_yaml(path, sources):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump({"version": 1, "sources": sources}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


class TestRegistryAtomicYaml:
    def test_save_writes_valid_yaml_no_tmp_left(self, tmp_path):
        """После save() sources.yaml валиден, временный файл не остаётся."""
        registry = SourceRegistry(sources_path=tmp_path / "sources.yaml")
        registry._sources = {
            "tg_test": SourceConfig(id="tg_test", type="telegram", url="https://t.me/s/test", enabled=True),
        }
        registry._loaded = True

        registry.save()

        assert (tmp_path / "sources.yaml").exists()
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert not tmp_files, f"Временные файлы не удалены: {tmp_files}"

        data = yaml.safe_load((tmp_path / "sources.yaml").read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert len(data["sources"]) == 1
        assert data["sources"][0]["id"] == "tg_test"

    def test_atomic_write_yaml_uses_temp_and_replace(self, tmp_path):
        """_atomic_write_yaml пишет через .tmp и заменяет целевой файл."""
        registry = SourceRegistry(sources_path=tmp_path / "sources.yaml")
        target = tmp_path / "sources.yaml"

        registry._atomic_write_yaml(target, {"version": 1, "sources": []})

        assert target.exists()
        assert not target.with_name(target.name + ".tmp").exists()

    def test_overwrite_yaml_atomic(self, tmp_path):
        """Повторная запись заменяет содержимое полностью, без остатков."""
        registry = SourceRegistry(sources_path=tmp_path / "sources.yaml")
        target = tmp_path / "sources.yaml"

        registry._atomic_write_yaml(target, {"version": 1, "sources": [{"id": "a"}]})
        registry._atomic_write_yaml(target, {"version": 1, "sources": [{"id": "b"}]})

        data = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert data["sources"] == [{"id": "b"}]

    def test_export_to_yaml_atomic(self, config, state):
        """_export_to_yaml записывает атомарно из SQLite."""
        registry = SourceRegistry(sources_path=config.sources_path, state=state)
        state.add_source("tg_exp", "telegram", "https://t.me/s/exp")
        registry._sources = {
            "tg_exp": SourceConfig(id="tg_exp", type="telegram", url="https://t.me/s/exp", enabled=True),
        }
        registry._loaded = True
        registry._use_sqlite = True

        registry._export_to_yaml()

        assert config.sources_path.exists()
        assert not config.sources_path.with_name(config.sources_path.name + ".tmp").exists()
        data = yaml.safe_load(config.sources_path.read_text(encoding="utf-8"))
        assert len(data["sources"]) == 1
