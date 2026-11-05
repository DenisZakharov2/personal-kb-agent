"""Тесты apply_config_proposal allowlist и атомарной записи (6.3).

Проверяется, что запись конфигурации ограничена строгим allowlist,
выполняется атомарно и с проверкой diff.
"""

from __future__ import annotations

from pathlib import Path

from kb_agent.core.self_improvement import (
    ALLOWED_CONFIG_PATHS,
    ImprovementProposal,
    SelfImprovementEngine,
)


class TestApplyConfigProposalAllowlist:
    """6.3: apply_config_proposal отклоняет пути вне allowlist."""

    def test_allowed_config_paths_non_empty(self):
        """Allowlist содержит ожидаемые конфигурационные файлы."""
        assert len(ALLOWED_CONFIG_PATHS) > 0
        assert "config/sources.example.yaml" in ALLOWED_CONFIG_PATHS

    def test_apply_config_rejects_arbitrary_path(self, state, config):
        """Попытка записи вне allowlist должна быть отклонена (возврат False)."""
        engine = SelfImprovementEngine(state)
        proposal = ImprovementProposal(
            proposal_id="test-config-1",
            target="config",
            target_name="/etc/passwd",
            current_version=None,
            proposed_version=None,
            diff="добавить вредоносную строку",
            reason="попытка записи вне allowlist",
        )

        ok = engine.apply_config_proposal(proposal, "malicious content", confirmed_by="attacker")
        assert ok is False
        # Предложение не сохранялось в SQLite (apply не сохраняет, только propose),
        # поэтому get_proposal может вернуть None — проверяем только возврат.

    def test_apply_config_rejects_relative_traversal(self, state, config):
        """Путь с ../ должен быть отклонён (вне allowlist)."""
        engine = SelfImprovementEngine(state)
        proposal = ImprovementProposal(
            proposal_id="test-config-2",
            target="config",
            target_name="../../../etc/passwd",
            current_version=None,
            proposed_version=None,
            diff="траверса Directory Traversal",
            reason="попытка обхода allowlist",
        )

        ok = engine.apply_config_proposal(proposal, "malicious", confirmed_by="attacker")
        assert ok is False

    def test_apply_config_accepts_allowed_path(self, state, config):
        """Путь из allowlist принимается (проверка allowlist проходит)."""
        from kb_agent.config import PROJECT_ROOT

        target = PROJECT_ROOT / "config" / "sources.example.yaml"
        original_content = target.read_text(encoding="utf-8") if target.exists() else None

        try:
            engine = SelfImprovementEngine(state)
            proposal = engine.propose_config_change(
                config_key="config/sources.example.yaml",
                old_value="",
                new_value="version: 1\nsources: []\n",
                reason="тест allowlist",
            )
            # target_name в предложении — это config_key
            assert proposal.target_name == "config/sources.example.yaml"

            ok = engine.apply_config_proposal(
                proposal, "version: 1\nsources: []\n", confirmed_by="tester"
            )
            assert ok is True
            stored = engine.get_proposal(proposal.proposal_id)
            assert stored is not None
            assert stored["status"] == "applied"
        finally:
            if original_content is not None:
                target.write_text(original_content, encoding="utf-8")
            elif target.exists():
                target.unlink()

    def test_apply_config_rejects_empty_diff(self, state, config):
        """Пустой diff — применение отменяется, предложение сохраняется как failed."""
        engine = SelfImprovementEngine(state)
        proposal = engine.propose_config_change(
            config_key="config/sources.example.yaml",
            old_value="",
            new_value="",
            reason="пустой diff",
        )
        # Подменяем diff на «нет изменений», чтобы проверка не прошла
        proposal.diff = "(изменений нет)"

        ok = engine.apply_config_proposal(proposal, "content", confirmed_by="tester")
        assert ok is False
        stored = engine.get_proposal(proposal.proposal_id)
        assert stored is not None
        assert stored["status"] == "failed"

    def test_apply_config_rejects_wrong_status(self, state, config):
        """Предложение не в статусе proposed не применяется."""
        engine = SelfImprovementEngine(state)
        proposal = ImprovementProposal(
            proposal_id="test-config-5",
            target="config",
            target_name="config/sources.example.yaml",
            current_version=None,
            proposed_version=None,
            diff="diff",
            reason="неверный статус",
            status="applied",
        )

        ok = engine.apply_config_proposal(proposal, "content", confirmed_by="tester")
        assert ok is False


class TestAtomicWrite:
    """6.3: атомарная запись конфигурации."""

    def test_atomic_write_no_temp_file_left(self, state, config):
        """После записи конфига временный .tmp файл не остаётся."""
        from kb_agent.config import PROJECT_ROOT

        target = PROJECT_ROOT / "config" / "sources.example.yaml"
        original = target.read_text(encoding="utf-8") if target.exists() else None

        try:
            engine = SelfImprovementEngine(state)
            proposal = engine.propose_config_change(
                config_key="config/sources.example.yaml",
                old_value="",
                new_value="version: 1\nsources: []\n",
                reason="проверка отсутствия .tmp",
            )
            engine.apply_config_proposal(
                proposal, "version: 1\nsources: []\n", confirmed_by="tester"
            )

            # Проверяем, что нет .tmp файлов рядом с целевым
            tmp_file = Path(str(target) + ".tmp")
            assert not tmp_file.exists(), f"Временный файл остался: {tmp_file}"
        finally:
            if original is not None:
                target.write_text(original, encoding="utf-8")
            elif target.exists():
                target.unlink()

    def test_atomic_write_writes_content(self, state, config):
        """Атомарная запись действительно пишет содержимое в файл."""
        from kb_agent.config import PROJECT_ROOT

        target = PROJECT_ROOT / "config" / "sources.example.yaml"
        original = target.read_text(encoding="utf-8") if target.exists() else None

        try:
            engine = SelfImprovementEngine(state)
            proposal = engine.propose_config_change(
                config_key="config/sources.example.yaml",
                old_value="",
                new_value="version: 2\nsources:\n  - id: test\n",
                reason="проверка записи",
            )
            ok = engine.apply_config_proposal(
                proposal, "version: 2\nsources:\n  - id: test\n", confirmed_by="tester"
            )
            assert ok is True
            written = target.read_text(encoding="utf-8")
            assert "version: 2" in written
            assert "id: test" in written
        finally:
            if original is not None:
                target.write_text(original, encoding="utf-8")
            elif target.exists():
                target.unlink()
