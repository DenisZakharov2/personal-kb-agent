"""Тесты CLI自我улучшения (kb-agent self-improve).

Покрывают подкоманды: list, propose-prompt, apply-prompt, reject,
propose-config, apply-config. follows паттерну tests/test_feedback.py.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from kb_agent.cli.main import app
import kb_agent.cli.commands.self_improve as si_mod


def _invoke(config, monkeypatch, args, json_output=True):
    monkeypatch.setattr(si_mod, "load_config", lambda: config)
    runner = CliRunner()
    args_with_json = list(args) + (["--json"] if json_output else [])
    return runner.invoke(app, args_with_json, prog_name="kb-agent")


def _parse(result):
    return json.loads(result.output)


class TestSelfImproveList:
    """kb-agent self-improve list — список предложений."""

    def test_list_empty(self, config, state, monkeypatch):
        """Без предложений — пустой список, код 0."""
        result = _invoke(config, monkeypatch, ["self-improve", "list"])
        assert result.exit_code == 0, result.output
        data = _parse(result)
        assert data["status"] == "proposed"
        assert data["count"] == 0
        assert data["proposals"] == []

    def test_list_with_data(self, config, state, monkeypatch):
        """Есть предложение — возвращается в списке."""
        state.save_proposal(
            proposal_id="prop-1",
            target="prompt",
            target_name="agent_prompt",
            current_version=1,
            proposed_version=2,
            diff="--- a/agent_prompt\n+++ b/agent_prompt\n",
            reason="улучшить суммаризацию",
        )
        result = _invoke(config, monkeypatch, ["self-improve", "list"])
        assert result.exit_code == 0, result.output
        data = _parse(result)
        assert data["count"] == 1
        assert data["proposals"][0]["proposal_id"] == "prop-1"
        assert data["proposals"][0]["target"] == "prompt"
        assert data["proposals"][0]["target_name"] == "agent_prompt"
        assert data["proposals"][0]["status"] == "proposed"

    def test_list_filter_by_status(self, config, state, monkeypatch):
        """Фильтр --status возвращает только предложения со статусом."""
        state.save_proposal(
            proposal_id="prop-rej",
            target="config",
            target_name="data/sources.yaml",
            current_version=None,
            proposed_version=None,
            diff="diff",
            reason="причина",
        )
        state.update_proposal("prop-rej", status="rejected")

        result = _invoke(config, monkeypatch, ["self-improve", "list", "--status", "rejected"])
        assert result.exit_code == 0, result.output
        data = _parse(result)
        assert data["status"] == "rejected"
        assert data["count"] == 1
        assert data["proposals"][0]["status"] == "rejected"

        # В статусе proposed этого предложения нет.
        result = _invoke(config, monkeypatch, ["self-improve", "list"])
        data = _parse(result)
        assert data["count"] == 0

    def test_list_invalid_status(self, config, state, monkeypatch):
        """Недопустимый статус — код 1 с ошибкой."""
        result = _invoke(
            config, monkeypatch, ["self-improve", "list", "--status", "bogus"]
        )
        assert result.exit_code == 1, result.output
        data = _parse(result)
        assert "error" in data

    def test_list_text_mode(self, config, state, monkeypatch):
        """Текстовый режим (без --json) работает и код 0."""
        result = _invoke(
            config, monkeypatch, ["self-improve", "list"], json_output=False
        )
        assert result.exit_code == 0, result.output
        assert "Предложений со статусом 'proposed' нет." in result.output


class TestSelfImproveProposePrompt:
    """kb-agent self-improve propose-prompt — создание предложения промпта."""

    def test_propose_prompt_creates(self, config, state, monkeypatch):
        """Создаётся предложение со статусом proposed и возвращается proposal_id."""
        result = _invoke(
            config,
            monkeypatch,
            ["self-improve", "propose-prompt", "agent_prompt", "нужна ясность", "новый текст промпта"],
        )
        assert result.exit_code == 0, result.output
        data = _parse(result)
        assert "proposal_id" in data
        assert data["target"] == "prompt"
        assert data["target_name"] == "agent_prompt"
        assert data["status"] == "proposed"
        assert data["reason"] == "нужна ясность"

    def test_propose_prompt_empty_content(self, config, state, monkeypatch):
        """Пустое содержимое — код 1 с ошибкой."""
        result = _invoke(
            config,
            monkeypatch,
            ["self-improve", "propose-prompt", "agent_prompt", "причина"],
        )
        assert result.exit_code == 1, result.output
        data = _parse(result)
        assert "error" in data


class TestSelfImproveApplyPrompt:
    """kb-agent self-improve apply-prompt — применение предложения промпта."""

    def test_apply_prompt_success(self, config, state, monkeypatch):
        """Успешное применение предложения (тестовый набор пропускается без заметок)."""
        result = _invoke(
            config,
            monkeypatch,
            ["self-improve", "propose-prompt", "agent_prompt", "причина", "контент v2"],
        )
        assert result.exit_code == 0, result.output
        proposal_id = _parse(result)["proposal_id"]

        result = _invoke(
            config,
            monkeypatch,
            ["self-improve", "apply-prompt", proposal_id, "контент v2"],
        )
        assert result.exit_code == 0, result.output
        data = _parse(result)
        assert data["proposal_id"] == proposal_id
        assert data["status"] == "applied"
        assert data["target_name"] == "agent_prompt"

        stored = state.get_proposal(proposal_id)
        assert stored["status"] == "applied"

    def test_apply_prompt_not_found(self, config, state, monkeypatch):
        """Неверный proposal_id — код 1."""
        result = _invoke(
            config,
            monkeypatch,
            ["self-improve", "apply-prompt", "несуществующий-id", "контент"],
        )
        assert result.exit_code == 1, result.output
        data = _parse(result)
        assert "error" in data
        assert "не найдено" in data["error"]

    def test_apply_prompt_empty_content(self, config, state, monkeypatch):
        """Пустое содержимое при применении — код 1."""
        result = _invoke(
            config,
            monkeypatch,
            ["self-improve", "apply-prompt", "любой-id"],
        )
        assert result.exit_code == 1, result.output
        data = _parse(result)
        assert "error" in data


class TestSelfImproveReject:
    """kb-agent self-improve reject — отклонение предложения."""

    def test_reject_success(self, config, state, monkeypatch):
        """Предложение отклоняется, статус становится rejected."""
        result = _invoke(
            config,
            monkeypatch,
            ["self-improve", "propose-prompt", "agent_prompt", "причина", "контент"],
        )
        assert result.exit_code == 0, result.output
        proposal_id = _parse(result)["proposal_id"]

        result = _invoke(
            config,
            monkeypatch,
            ["self-improve", "reject", proposal_id],
        )
        assert result.exit_code == 0, result.output
        data = _parse(result)
        assert data["proposal_id"] == proposal_id
        assert data["status"] == "rejected"

        stored = state.get_proposal(proposal_id)
        assert stored["status"] == "rejected"

    def test_reject_not_found(self, config, state, monkeypatch):
        """Неверный ID — код 1."""
        result = _invoke(
            config, monkeypatch, ["self-improve", "reject", "несуществующий-id"]
        )
        assert result.exit_code == 1, result.output
        data = _parse(result)
        assert "error" in data
        assert "не найдено" in data["error"]


class TestSelfImproveProposeConfig:
    """kb-agent self-improve propose-config — создание предложения конфига."""

    def test_propose_config_creates(self, config, state, monkeypatch):
        """Создаётся предложение изменения конфига со статусом proposed."""
        result = _invoke(
            config,
            monkeypatch,
            [
                "self-improve",
                "propose-config",
                "data/sources.yaml",
                "старое значение",
                "новое значение",
                "причина изменения",
            ],
        )
        assert result.exit_code == 0, result.output
        data = _parse(result)
        assert "proposal_id" in data
        assert data["target"] == "config"
        assert data["target_name"] == "data/sources.yaml"
        assert data["status"] == "proposed"
        assert data["reason"] == "причина изменения"


class TestSelfImproveApplyConfig:
    """kb-agent self-improve apply-config — применение предложения конфига."""

    def test_apply_config_success(self, config, state, monkeypatch):
        """Успешное применение предложения конфига (файл записывается, data/ в gitignore)."""
        result = _invoke(
            config,
            monkeypatch,
            [
                "self-improve",
                "propose-config",
                "data/sources.yaml",
                "старое",
                "новое",
                "причина",
            ],
        )
        assert result.exit_code == 0, result.output
        proposal_id = _parse(result)["proposal_id"]

        result = _invoke(
            config,
            monkeypatch,
            ["self-improve", "apply-config", proposal_id, "итоговое значение"],
        )
        assert result.exit_code == 0, result.output
        data = _parse(result)
        assert data["proposal_id"] == proposal_id
        assert data["status"] == "applied"

        stored = state.get_proposal(proposal_id)
        assert stored["status"] == "applied"

    def test_apply_config_not_found(self, config, state, monkeypatch):
        """Неверный proposal_id — код 1."""
        result = _invoke(
            config,
            monkeypatch,
            ["self-improve", "apply-config", "несуществующий-id", "значение"],
        )
        assert result.exit_code == 1, result.output
        data = _parse(result)
        assert "error" in data
        assert "не найдено" in data["error"]
