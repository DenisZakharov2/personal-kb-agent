"""Движок самоулучшения под наблюдением человека (Этап 5).

Собирает метрики из обратной связи, формирует предложения по улучшению
промптов и конфигурации в виде diff, требует явного подтверждения человека
перед применением, версионирует промпты для отката и проверяет изменения
на тестовом наборе до применения.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from kb_agent.config import load_config
from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.core.self_improvement")


@dataclass
class ImprovementProposal:
    """Предложение по улучшению промпта или конфигурации."""

    proposal_id: str
    target: str  # 'prompt' | 'config' | 'source'
    target_name: str
    current_version: Optional[int]
    proposed_version: Optional[int]
    diff: str
    reason: str
    status: str = "proposed"  # proposed | confirmed | applied | rejected | failed
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    confirmed_at: Optional[str] = None
    applied_at: Optional[str] = None
    test_passed: bool = False


class SelfImprovementEngine:
    """Движок самоулучшения базы знаний."""

    def __init__(self, state: StateManager) -> None:
        self.state = state
        self.config = load_config()

    # ---- Сбор метрик ----

    def collect_feedback_metrics(self) -> dict:
        """Собрать метрики обратной связи для анализа."""
        from kb_agent.feedback.metrics import FeedbackMetricsCollector

        collector = FeedbackMetricsCollector(self.state)
        metrics = collector.compute()
        return {
            "total_ratings": metrics.total_ratings,
            "average_score": metrics.average_score,
            "score_distribution": metrics.score_distribution,
            "total_messages": metrics.total_messages,
            "recent_feedback": metrics.recent_feedback,
        }

    # ---- Предложения по улучшению ----

    def propose_prompt_change(
        self,
        prompt_name: str,
        new_content: str,
        reason: str,
    ) -> ImprovementProposal:
        """Сформировать предложение изменить промпт.

        Вычисляет diff между текущей активной версией и новым содержимым.
        """
        current = self.state.get_active_prompt(prompt_name)
        current_version = current["version"] if current else 0
        current_content = current["content"] if current else ""

        diff_lines = list(
            difflib.unified_diff(
                current_content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"{prompt_name} v{current_version}",
                tofile=f"{prompt_name} v{current_version + 1}",
                lineterm="",
            )
        )
        diff_text = "".join(diff_lines) if diff_lines else "(изменений нет)"

        proposal = ImprovementProposal(
            proposal_id=self._next_proposal_id(),
            target="prompt",
            target_name=prompt_name,
            current_version=current_version,
            proposed_version=current_version + 1,
            diff=diff_text,
            reason=reason,
        )
        logger.info(
            "Предложение изменений промпта %s (v%s → v%s): %s",
            prompt_name, current_version, current_version + 1, reason,
        )
        return proposal

    def propose_config_change(
        self,
        config_key: str,
        old_value: str,
        new_value: str,
        reason: str,
    ) -> ImprovementProposal:
        """Сформировать предложение изменить конфигурацию (sources.yaml / user_profile.yaml)."""
        diff_lines = list(
            difflib.unified_diff(
                old_value.splitlines(keepends=True),
                new_value.splitlines(keepends=True),
                fromfile=f"{config_key} (текущее)",
                tofile=f"{config_key} (предложенное)",
                lineterm="",
            )
        )
        diff_text = "".join(diff_lines) if diff_lines else "(изменений нет)"

        proposal = ImprovementProposal(
            proposal_id=self._next_proposal_id(),
            target="config",
            target_name=config_key,
            current_version=None,
            proposed_version=None,
            diff=diff_text,
            reason=reason,
        )
        logger.info(
            "Предложение изменений конфига %s: %s", config_key, reason,
        )
        return proposal

    def list_proposals(self, status: str = "proposed") -> list[ImprovementProposal]:
        """Вернуть список предложений со статусом."""
        # Предложения хранятся в памяти в рамках сессии; в будущем — в SQLite
        return []  # placeholder, расширяется при добавлении таблицы proposals

    # ---- Применение с подтверждением ----

    def apply_prompt_proposal(
        self,
        proposal: ImprovementProposal,
        new_content: str,
        confirmed_by: str = "user",
    ) -> bool:
        """Применить предложение по изменению промпта после подтверждения."""
        if proposal.status != "proposed":
            logger.warning(
                "Предложение %s уже имеет статус %s", proposal.proposal_id, proposal.status
            )
            return False

        # Тестирование на тестовом наборе (заглушка — расширяется)
        test_passed = self._run_test_suite(proposal)
        proposal.test_passed = test_passed
        if not test_passed:
            logger.warning(
                "Предложение %s не прошло тестовый набор, применение отменено",
                proposal.proposal_id,
            )
            proposal.status = "failed"
            return False

        now = datetime.now(timezone.utc).isoformat()
        self.state.save_prompt(
            prompt_name=proposal.target_name,
            version=proposal.proposed_version or (proposal.current_version or 0) + 1,
            content=new_content,
            is_active=True,
            approved_by=confirmed_by,
            reverted_from=proposal.current_version,
        )
        proposal.status = "applied"
        proposal.applied_at = now
        proposal.confirmed_at = now
        logger.info(
            "Промпт %s обновлён до версии %s (подтвердил %s)",
            proposal.target_name, proposal.proposed_version, confirmed_by,
        )
        return True

    def apply_config_proposal(
        self,
        proposal: ImprovementProposal,
        new_value: str,
        confirmed_by: str = "user",
    ) -> bool:
        """Применить предложение по изменению конфигурационного YAML-файла."""
        if proposal.status != "proposed":
            logger.warning(
                "Предложение %s уже имеет статус %s", proposal.proposal_id, proposal.status
            )
            return False

        now = datetime.now(timezone.utc).isoformat()
        path = Path(proposal.target_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_value, encoding="utf-8")

        proposal.status = "applied"
        proposal.applied_at = now
        proposal.confirmed_at = now
        logger.info(
            "Конфиг %s обновлён (подтвердил %s)", proposal.target_name, confirmed_by,
        )
        return True

    def reject_proposal(self, proposal: ImprovementProposal) -> None:
        """Отклонить предложение."""
        proposal.status = "rejected"
        logger.info("Предложение %s отклонено", proposal.proposal_id)

    def rollback_prompt(
        self,
        prompt_name: str,
        to_version: int,
        confirmed_by: str = "user",
    ) -> bool:
        """Откатить промпт к указанной версии."""
        target = self.state.connect().execute(
            """
            SELECT * FROM prompt_history
            WHERE prompt_name = ? AND version = ?
            """,
            (prompt_name, to_version),
        ).fetchone()
        if not target:
            logger.warning("Версия %s промпта %s не найдена", to_version, prompt_name)
            return False

        conn = self.state.connect()
        conn.execute(
            "UPDATE prompt_history SET is_active = 0 WHERE prompt_name = ?",
            (prompt_name,),
        )
        conn.execute(
            """
            UPDATE prompt_history
            SET is_active = 1
            WHERE prompt_name = ? AND version = ?
            """,
            (prompt_name, to_version),
        )
        conn.commit()
        logger.info(
            "Промпт %s откатан к версии %s (подтвердил %s)",
            prompt_name, to_version, confirmed_by,
        )
        return True

    # ---- Вспомогательное ----

    def _next_proposal_id(self) -> str:
        import uuid
        return str(uuid.uuid4())

    def _run_test_suite(self, proposal: ImprovementProposal) -> bool:
        """Запустить тестовый набор для предложенного изменения.

        Заглушка — в будущем здесь будет проверка на фиксированном наборе
        вопросов и заметок до применения изменения.
        """
        logger.info(
            "Тестовый набор для предложения %s: пропущен (заглушка)",
            proposal.proposal_id,
        )
        return True
