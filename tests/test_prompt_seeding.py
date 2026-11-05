"""Тесты сеяния системных промптов (R-03 аудита).

Проверяют, что agent_prompt.md импортируется в prompt_history как
version=1, is_active=1 при первом запуске, и что активная версия
читается из БД (для self-improve rollback и kb_revert_prompt).
"""

from __future__ import annotations

from typer.testing import CliRunner

import kb_agent.cli.commands.interview as interview_mod
from kb_agent.cli.main import app
from kb_agent.core.interview import InterviewManager
from kb_agent.core.prompts import (
    ANSWER_PROMPT_NAME,
    DEFAULT_PROMPT_NAME,
    SUMMARIZE_PROMPT_NAME,
    ensure_prompt_seeded,
    get_active_system_prompt,
)


def _invoke_interview_init(config, monkeypatch):
    monkeypatch.setattr(interview_mod, "load_config", lambda: config)
    runner = CliRunner()
    return runner.invoke(app, ["interview", "init"], prog_name="kb-agent")


class TestPromptSeeding:
    """Импорт agent_prompt.md в prompt_history."""

    def test_ensure_prompt_seeded_creates_three_prompts(self, config, state):
        """После ensure_prompt_seeded в prompt_history 3 активные версии v1."""
        ensure_prompt_seeded(config, state)

        for prompt_name in (DEFAULT_PROMPT_NAME, SUMMARIZE_PROMPT_NAME, ANSWER_PROMPT_NAME):
            row = state.get_active_prompt(prompt_name)
            assert row is not None, f"{prompt_name}: активная версия не найдена"
            assert row["version"] == 1, f"{prompt_name}: ожидается версия 1, got {row['version']}"
            assert row["is_active"] == 1
            assert row["content"].strip(), f"{prompt_name}: содержимое пустое"
            assert row["approved_by"] == "system"

    def test_ensure_prompt_seeded_idempotent(self, config, state):
        """Повторный вызов не создаёт дубли."""
        ensure_prompt_seeded(config, state)
        ensure_prompt_seeded(config, state)

        # Проверяем, что только по одной активной версии на промпт.
        conn = state.connect()
        for prompt_name in (DEFAULT_PROMPT_NAME, SUMMARIZE_PROMPT_NAME, ANSWER_PROMPT_NAME):
            rows = conn.execute(
                "SELECT COUNT(*) AS c FROM prompt_history WHERE prompt_name = ? AND is_active = 1",
                (prompt_name,),
            ).fetchone()["c"]
            assert rows == 1, f"{prompt_name}: ожидается 1 активная версия, got {rows}"

    def test_get_active_system_prompt_returns_content(self, config, state):
        """get_active_system_prompt возвращает содержимое активной версии."""
        ensure_prompt_seeded(config, state)

        content = get_active_system_prompt(state, DEFAULT_PROMPT_NAME)
        assert content is not None
        assert len(content) > 0
        assert "ассистент" in content.lower() or "баз" in content.lower()

    def test_get_active_system_prompt_missing(self, config, state):
        """Без сидирования get_active_system_prompt возвращает None."""
        content = get_active_system_prompt(state, DEFAULT_PROMPT_NAME)
        assert content is None


class TestInterviewInitSeedsPrompts:
    """kb-agent interview init сеит промпты в prompt_history."""

    def test_interview_init_seeds_prompts(self, config, state, monkeypatch):
        """После interview init в prompt_history есть активные версии промптов."""
    def test_interview_init_seeds_prompts(self, config, state, monkeypatch):
        """После interview init в prompt_history есть активные версии промптов."""
        monkeypatch.setattr(interview_mod, "load_config", lambda: config)

        # Замокаем интерактивное интервью — оно не должно запрашивать ввод.
        def fake_run_init(self):
            return {
                "goal": "тестовая цель",
                "tasks": "тестовые задачи",
                "topics": ["Тест"],
                "sources": [],
                "frequency": "daily",
                "system": "Тест",
            }

        monkeypatch.setattr(InterviewManager, "run_init_interview", fake_run_init)
        monkeypatch.setattr(InterviewManager, "profile_exists", lambda self: False)
        monkeypatch.setattr(
            InterviewManager,
            "save_profile",
            lambda self, profile: config.profile_path,
        )

        runner = CliRunner()
        result = runner.invoke(app, ["interview", "init"], prog_name="kb-agent", input="\n")
        assert result.exit_code == 0, result.output

        for prompt_name in (DEFAULT_PROMPT_NAME, SUMMARIZE_PROMPT_NAME, ANSWER_PROMPT_NAME):
            row = state.get_active_prompt(prompt_name)
            assert row is not None, f"{prompt_name}: не сеяно после interview init"
            assert row["is_active"] == 1
