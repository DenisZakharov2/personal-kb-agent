"""Тесты самоулучшения (P1-10): предложения в SQLite, атомарное применение."""

from __future__ import annotations

from kb_agent.core.self_improvement import ImprovementProposal, SelfImprovementEngine


class TestSelfImprovementProposals:
    """P1-10: предложения хранятся в SQLite, а не возвращают []."""

    def test_list_proposals_empty_initially(self, state, config):
        """До создания предложений список пуст."""
        engine = SelfImprovementEngine(state)
        assert engine.list_proposals() == []

    def test_propose_prompt_change_stored_in_sqlite(self, state, config):
        """propose_prompt_change сохраняет предложение в prompt_proposals."""
        engine = SelfImprovementEngine(state)
        proposal = engine.propose_prompt_change(
            "agent_prompt", "Новое содержимое промпта v2", "улучшить ясность ответов"
        )

        assert proposal.proposal_id
        assert proposal.status == "proposed"
        assert proposal.target_name == "agent_prompt"
        assert proposal.proposed_version == 1

        # Предложение читается из SQLite
        stored = engine.get_proposal(proposal.proposal_id)
        assert stored is not None
        assert stored["status"] == "proposed"
        assert stored["target_name"] == "agent_prompt"

        proposals = engine.list_proposals(status="proposed")
        assert len(proposals) == 1
        assert proposals[0].proposal_id == proposal.proposal_id

    def test_apply_prompt_proposal_atomic_activation(self, state, config):
        """apply_prompt_proposal активирует новую версию и деактивирует старую в одной транзакции."""
        engine = SelfImprovementEngine(state)
        state.save_prompt(
            prompt_name="agent_prompt",
            version=1,
            content="содержимое версии 1",
            is_active=True,
            approved_by="system",
        )

        proposal = engine.propose_prompt_change(
            "agent_prompt", "содержимое версии 2", "тест атомарного применения"
        )
        assert proposal.status == "proposed"
        assert proposal.proposed_version == 2

        ok = engine.apply_prompt_proposal(proposal, "содержимое версии 2", confirmed_by="tester")
        assert ok is True

        # Активна только версия 2
        active = state.get_active_prompt("agent_prompt")
        assert active is not None
        assert active["version"] == 2
        assert active["content"] == "содержимое версии 2"
        assert active["approved_by"] == "tester"

        # Ровно одна активная версия (старая деактивирована в той же транзакции)
        conn = state.connect()
        count = conn.execute(
            """
            SELECT COUNT(*) FROM prompt_history
            WHERE prompt_name = ? AND is_active = 1
            """,
            ("agent_prompt",),
        ).fetchone()[0]
        assert count == 1

        # Предложение перешло в applied
        stored = engine.get_proposal(proposal.proposal_id)
        assert stored["status"] == "applied"

    def test_apply_prompt_proposal_wrong_status_rejected(self, state, config):
        """Предложение не в статусе proposed не применяется."""
        engine = SelfImprovementEngine(state)
        proposal = ImprovementProposal(
            proposal_id="test-apply-2",
            target="prompt",
            target_name="agent_prompt",
            current_version=None,
            proposed_version=1,
            diff="diff",
            reason="тест",
            status="applied",
        )
        assert engine.apply_prompt_proposal(proposal, "контент") is False

    def test_run_test_suite_no_changes(self, state, config):
        """_run_test_suite возвращает False, если diff пустой / нет изменений."""
        engine = SelfImprovementEngine(state)
        proposal = ImprovementProposal(
            proposal_id="x",
            target="prompt",
            target_name="agent_prompt",
            current_version=1,
            proposed_version=2,
            diff="(изменений нет)",
            reason="тест",
        )
        assert engine._run_test_suite(proposal) is False

    def test_apply_fails_when_no_changes(self, state, config):
        """Применение отменяется, если тестовый набор не пройден."""
        engine = SelfImprovementEngine(state)
        proposal = engine.propose_prompt_change("agent_prompt", "контент без изменений", "тест")
        # Подменем diff на «нет изменений», чтобы тестовый набор не прошёл
        proposal.diff = "(изменений нет)"
        assert engine.apply_prompt_proposal(proposal, "контент") is False
        stored = engine.get_proposal(proposal.proposal_id)
        assert stored is not None
        assert stored["status"] == "failed"
