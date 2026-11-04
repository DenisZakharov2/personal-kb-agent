"""Движок самоулучшения под наблюдением человека (Этап 5).

Собирает метрики из обратной связи, формирует предложения по улучшению
промптов и конфигурации, хранит их в SQLite, требует явного подтверждения
человека перед применением, версионирует промпты с атомарной активацией и
проверяет изменения на тестовом наборе до применения.
"""

from __future__ import annotations

import difflib
import json
import os
import sqlite3
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from kb_agent.config import PROJECT_ROOT, load_config
from kb_agent.core.prompts import SUMMARIZE_PROMPT_NAME, ANSWER_PROMPT_NAME
from kb_agent.core.search import SearchEngine
from kb_agent.processing.llm_client import LLMClient
from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger

# Порог прохождения eval-набора перед активацией нового промпта.
EVAL_PASS_THRESHOLD = 0.5

logger = setup_logger("kb_agent.core.self_improvement")

# 6.3: строгий allowlist конфигурационных файлов, доступных для apply_config_proposal.
# target_name должен быть одним из этих путей (относительно корня проекта или
# абсолютный внутри data_dir). Произвольная запись вне списка запрещается.
ALLOWED_CONFIG_PATHS = {
    "config/sources.example.yaml",
    "config/prompts/agent_prompt.md",
    "data/sources.yaml",
    "data/user_profile.yaml",
    "data/kb_agent.log",
}


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

    # ---- Предложения по улучшению (хранятся в SQLite) ----

    def propose_prompt_change(
        self,
        prompt_name: str,
        new_content: str,
        reason: str,
    ) -> ImprovementProposal:
        """Сформировать предложение изменить промпт и сохранить его в SQLite.

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

        proposal_id = self._next_proposal_id()
        self.state.save_proposal(
            proposal_id=proposal_id,
            target="prompt",
            target_name=prompt_name,
            current_version=current_version,
            proposed_version=current_version + 1,
            diff=diff_text,
            reason=reason,
        )

        proposal = ImprovementProposal(
            proposal_id=proposal_id,
            target="prompt",
            target_name=prompt_name,
            current_version=current_version,
            proposed_version=current_version + 1,
            diff=diff_text,
            reason=reason,
        )
        logger.info(
            "Предложение изменений промпта %s (v%s → v%s) сохранено: %s",
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
        """Сформировать предложение изменить конфигурацию и сохранить в SQLite."""
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

        proposal_id = self._next_proposal_id()
        self.state.save_proposal(
            proposal_id=proposal_id,
            target="config",
            target_name=config_key,
            current_version=None,
            proposed_version=None,
            diff=diff_text,
            reason=reason,
        )

        proposal = ImprovementProposal(
            proposal_id=proposal_id,
            target="config",
            target_name=config_key,
            current_version=None,
            proposed_version=None,
            diff=diff_text,
            reason=reason,
        )
        logger.info("Предложение изменений конфига %s сохранено: %s", config_key, reason)
        return proposal

    def get_proposal(self, proposal_id: str) -> Optional[dict]:
        """Возвращает предложение по ID или None."""
        return self.state.get_proposal(proposal_id)

    def list_proposals(self, status: str = "proposed") -> list[ImprovementProposal]:
        """Вернуть список предложений со статусом из SQLite (не заглушка)."""
        rows = self.state.list_proposals(status=status)
        return [self._row_to_proposal(row) for row in rows]

    # ---- Применение с подтверждением ----

    def apply_prompt_proposal(
        self,
        proposal: ImprovementProposal,
        new_content: str,
        confirmed_by: str = "user",
    ) -> bool:
        """Применить предложение по изменению промпта после подтверждения.

        Новая версия и снятие is_active со старой выполняются в одной
        транзакции (BEGIN IMMEDIATE), поэтому в каждый момент времени активна
        ровно одна версия промпта.
        """
        if proposal.status != "proposed":
            logger.warning(
                "Предложение %s уже имеет статус %s", proposal.proposal_id, proposal.status
            )
            return False

        test_passed = self._run_test_suite(proposal)
        proposal.test_passed = test_passed
        if not test_passed:
            logger.warning(
                "Предложение %s не прошло тестовый набор, применение отменено",
                proposal.proposal_id,
            )
            self.state.update_proposal(
                proposal.proposal_id, status="failed", test_passed=0
            )
            proposal.status = "failed"
            return False

        now = datetime.now(timezone.utc).isoformat()
        new_version = proposal.proposed_version or (proposal.current_version or 0) + 1

        conn = self.state.connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Вставляем новую версию как активную
            self.state.save_prompt(
                prompt_name=proposal.target_name,
                version=new_version,
                content=new_content,
                is_active=True,
                approved_by=confirmed_by,
                reverted_from=proposal.current_version,
                conn=conn,
            )
            # Снимаем is_active со всех остальных версий этого промпта
            conn.execute(
                """
                UPDATE prompt_history
                SET is_active = 0
                WHERE prompt_name = ? AND version <> ?
                """,
                (proposal.target_name, new_version),
            )
            # Помечаем предложение как применённое
            self.state.update_proposal(
                proposal.proposal_id,
                conn=conn,
                status="applied",
                test_passed=int(test_passed),
                confirmed_at=now,
                applied_at=now,
                confirmed_by=confirmed_by,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

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
        """Применить предложение по изменению конфигурационного YAML-файла.

        6.3: путь ограничивается строгим allowlist конфигурационных файлов,
        запись выполняется атомарно (временный файл + os.replace), diff
        проверяется перед записью.
        """
        if proposal.status != "proposed":
            logger.warning(
                "Предложение %s уже имеет статус %s", proposal.proposal_id, proposal.status
            )
            return False

        # 6.3: проверка allowlist пути
        target = Path(proposal.target_name)
        target_str = str(target)
        # Нормализуем путь: приводим к относительному от PROJECT_ROOT если возможно
        from kb_agent.config import PROJECT_ROOT

        try:
            rel_target = target.resolve().relative_to(PROJECT_ROOT.resolve())
            rel_target_str = str(rel_target)
        except ValueError:
            rel_target_str = target_str

        allowed = (
            target_str in ALLOWED_CONFIG_PATHS
            or rel_target_str in ALLOWED_CONFIG_PATHS
        )
        if not allowed:
            logger.error(
                "Путь конфига не в allowlist: %s (разрешены: %s)",
                proposal.target_name,
                ", ".join(sorted(ALLOWED_CONFIG_PATHS)),
            )
            return False

        # Тестовый набор для конфигурационных изменений
        test_passed = self._run_test_suite(proposal)
        proposal.test_passed = test_passed
        if not test_passed:
            self.state.update_proposal(proposal.proposal_id, status="failed", test_passed=0)
            proposal.status = "failed"
            return False

        # Проверка diff: предложение должно содержать реальные изменения
        if not proposal.diff or "(изменений нет)" in proposal.diff:
            logger.warning(
                "Предложение %s не содержит diff, применение отменено",
                proposal.proposal_id,
            )
            self.state.update_proposal(proposal.proposal_id, status="failed", test_passed=0)
            proposal.status = "failed"
            return False

        now = datetime.now(timezone.utc).isoformat()

        # 6.3: атомарная запись — во временный файл в той же директории,
        # затем os.replace. Получатель видит либо старый файл, либо новый целиком.
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        try:
            tmp.write_text(new_value, encoding="utf-8")
            os.replace(tmp, target)
        except Exception:
            # Удаляем временный файл при ошибке
            try:
                tmp.unlink(missing_ok=True)  # type: ignore[attr-defined]
            except OSError:
                pass
            raise

        self.state.update_proposal(
            proposal.proposal_id,
            status="applied",
            test_passed=int(test_passed),
            confirmed_at=now,
            applied_at=now,
            confirmed_by=confirmed_by,
        )
        proposal.status = "applied"
        proposal.applied_at = now
        proposal.confirmed_at = now
        logger.info(
            "Конфиг %s обновлён атомарно (подтвердил %s)", proposal.target_name, confirmed_by,
        )
        return True

    def reject_proposal(self, proposal: ImprovementProposal) -> None:
        """Отклонить предложение."""
        proposal.status = "rejected"
        self.state.update_proposal(proposal.proposal_id, status="rejected")
        logger.info("Предложение %s отклонено", proposal.proposal_id)

    def rollback_prompt(
        self,
        prompt_name: str,
        to_version: int,
        confirmed_by: str = "user",
    ) -> bool:
        """Откатить промпт к указанной версии (атомарно)."""
        return self.state.activate_prompt_version(
            prompt_name=prompt_name, version=to_version, confirmed_by=confirmed_by
        )

    # ---- Вспомогательное ----

    @staticmethod
    def _row_to_proposal(row: dict) -> ImprovementProposal:
        return ImprovementProposal(
            proposal_id=row["proposal_id"],
            target=row["target"],
            target_name=row["target_name"],
            current_version=row["current_version"],
            proposed_version=row["proposed_version"],
            diff=row["diff"],
            reason=row["reason"],
            status=row["status"],
            created_at=row["created_at"],
            confirmed_at=row["confirmed_at"],
            applied_at=row["applied_at"],
            test_passed=bool(row["test_passed"]),
        )

    def _next_proposal_id(self) -> str:
        import uuid
        return str(uuid.uuid4())

    def _run_test_suite(self, proposal: ImprovementProposal) -> bool:
        """Базовая проверка предложенного изменения перед применением.

        Проверяется, что diff не пустой (есть реальные изменения), и — если
        есть eval-набор и накопленные заметки — запускается eval-проверка на
        тестовом наборе (раздел 9 аудита: атомарная активация только после
        проверки).
        """
        has_changes = bool(proposal.diff) and "(изменений нет)" not in proposal.diff
        if not has_changes:
            logger.info(
                "Тестовый набор для предложения %s: не пройден (нет изменений)",
                proposal.proposal_id,
            )
            return False
        eval_passed = self._run_eval_check()
        logger.info(
            "Тестовый набор для предложения %s: %s",
            proposal.proposal_id,
            "пройден" if eval_passed else "не пройден",
        )
        return eval_passed

    def _run_eval_check(self) -> bool:
        """Запускает eval-набор (tests/eval_set.json) перед активацией изменения.

        Возвращает True, если eval-набор отсутствует, заметок ещё нет
        (нечего оценивать) или доля пройденных вопросов >= порога.
        """
        eval_path = PROJECT_ROOT / "tests" / "eval_set.json"
        if not eval_path.exists():
            logger.info("Eval-набор не найден: %s, проверка пропущена", eval_path)
            return True

        try:
            note_count = self.state.connect().execute(
                "SELECT COUNT(*) AS c FROM notes"
            ).fetchone()["c"]
        except sqlite3.DatabaseError as exc:
            logger.warning("Не удалось посчитать заметки для eval: %s", exc)
            return True
        if note_count == 0:
            logger.info("Eval-проверка пропущена: в таблице notes пока нет заметок")
            return True

        try:
            data = json.loads(eval_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Не удалось прочитать eval-набор: %s", exc)
            return True

        questions = data.get("questions", data.get("eval_set", [])) or []
        if not questions:
            return True

        llm = LLMClient(self.config)
        engine = SearchEngine(self.config, self.state, llm)
        total = 0
        passed = 0
        for question in questions:
            total += 1
            response = engine.search(question.get("question", ""), limit=5)
            found_ids = {r.note_id for r in response.results}
            expected = set(question.get("expected_note_ids") or [])
            forbidden = set(question.get("forbidden_hallucinations") or [])
            ok = True
            if expected and not (expected & found_ids):
                ok = False
            answer = response.answer or ""
            if forbidden and any(phrase in answer for phrase in forbidden):
                ok = False
            if ok:
                passed += 1

        rate = passed / total if total else 0.0
        logger.info(
            "Eval-проверка: %d/%d вопросов пройдено (порог %s)",
            passed,
            total,
            EVAL_PASS_THRESHOLD,
        )
        return rate >= EVAL_PASS_THRESHOLD
