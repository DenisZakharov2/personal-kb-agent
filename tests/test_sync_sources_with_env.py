"""Регрессионные тесты scripts/sync_sources_with_env.py.

Главный сценарий: пустая конфигурация окружения (например, секреты CI
не заполнены) не должна приводить к отключению всех включённых источников.
"""
# ruff: noqa

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from kb_agent.sources.base import SourceConfig
from kb_agent.sources.registry import SourceRegistry


@pytest.fixture
def sync_script() -> ModuleType:
    """Импортирует scripts/sync_sources_with_env.py как обычный модуль."""
    path = Path(__file__).resolve().parent.parent / "scripts" / "sync_sources_with_env.py"
    spec = importlib.util.spec_from_file_location("sync_sources_with_env", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def _clean_env(monkeypatch):
    """Убирает переменные окружения каналов, чтобы тесты были детерминированы."""
    monkeypatch.delenv("KB_TELEGRAM_CHANNELS", raising=False)
    monkeypatch.delenv("KB_YOUTUBE_CHANNELS", raising=False)


@pytest.fixture
def _argv_ok(monkeypatch):
    """argparse в скрипте читает sys.argv — подменяем на нейтральный argv."""
    monkeypatch.setattr(sys, "argv", ["sync_sources_with_env.py"])


def _enabled_ids(st) -> set[str]:
    return {row["source_id"] for row in st.list_sources() if row.get("enabled")}


class TestConfiguredSourceIds:
    def test_empty_env_yields_empty_set(self, sync_script, _clean_env):
        assert sync_script.configured_source_ids() == set()

    def test_telegram_and_youtube_parsed(self, sync_script, _clean_env, monkeypatch):
        monkeypatch.setenv("KB_TELEGRAM_CHANNELS", "https://t.me/s/ch_one")
        monkeypatch.setenv("KB_YOUTUBE_CHANNELS", "https://youtube.com/@chan")
        assert sync_script.configured_source_ids() == {"tg_ch_one", "yt_chan"}

    def test_invalid_entries_ignored(self, sync_script, _clean_env, monkeypatch):
        monkeypatch.setenv("KB_TELEGRAM_CHANNELS", "https://t.me/s/good, @@bad,,")
        assert sync_script.configured_source_ids() == {"tg_good"}


class TestEmptyEnvGuard:
    def test_empty_env_does_not_disable_sources(
        self, sync_script, state, config, _clean_env, _argv_ok, monkeypatch
    ):
        """Пустая конфигурация окружения не должна отключать все источники."""
        monkeypatch.setattr(sync_script, "load_config", lambda: config)

        registry = SourceRegistry(sources_path=config.sources_path, state=state)
        registry.add(
            SourceConfig(id="tg_keepme", type="telegram", url="https://t.me/s/keepme", enabled=True)
        )

        assert sync_script.main() == 0
        assert _enabled_ids(state) == {"tg_keepme"}

    def test_source_removed_from_env_gets_disabled(
        self, sync_script, state, config, _clean_env, _argv_ok, monkeypatch
    ):
        """Обычное поведение сохраняется: источник, убранный из окружения, отключается."""
        monkeypatch.setattr(sync_script, "load_config", lambda: config)
        monkeypatch.setenv("KB_TELEGRAM_CHANNELS", "https://t.me/s/still_here")

        registry = SourceRegistry(sources_path=config.sources_path, state=state)
        registry.add(
            SourceConfig(
                id="tg_still_here", type="telegram", url="https://t.me/s/still_here", enabled=True
            )
        )
        registry.add(
            SourceConfig(
                id="tg_removed", type="telegram", url="https://t.me/s/removed", enabled=True
            )
        )

        assert sync_script.main() == 0
        assert _enabled_ids(state) == {"tg_still_here"}

    def test_no_sources_in_db_is_noop(
        self, sync_script, state, config, _clean_env, _argv_ok, monkeypatch
    ):
        monkeypatch.setattr(sync_script, "load_config", lambda: config)
        assert sync_script.main() == 0
