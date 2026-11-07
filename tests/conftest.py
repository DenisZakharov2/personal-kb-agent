"""Общие фикстуры для тестов kb-agent."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from kb_agent.config import AppConfig
from kb_agent.storage.state import StateManager


@pytest.fixture
def temp_dir():
    """Временная директория для тестов."""
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def config(temp_dir):
    """Тестовая конфигурация с изолированными путями."""
    return AppConfig(
        vault_path=temp_dir / "vault",
        data_dir=temp_dir / "data",
        db_path=temp_dir / "data" / "state.db",
        sources_path=temp_dir / "data" / "sources.yaml",
        profile_path=temp_dir / "data" / "user_profile.yaml",
        log_path=temp_dir / "data" / "kb_agent.log",
        prompts_dir=temp_dir / "config" / "prompts",
        llm_provider="ollama",
        openai_api_key=None,
        openai_model="test-model",
        openai_temperature=0.0,
        openai_max_tokens=1000,
        telegram_channels="",
        youtube_channels="",
        yt_months=1,
        yt_sub_langs="ru,en",
        limit=10,
    )


@pytest.fixture
def state(config):
    """Инициализированный StateManager для тестов."""
    s = StateManager(config.db_path)
    s.init_db()
    yield s
    s.close()
