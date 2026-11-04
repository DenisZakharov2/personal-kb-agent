"""Метрики обратной связи (Этап 5).

Подсчёт полезности заметок, процента пропусков, точности суммаризации
и прочих показателей качества на основе собранной обратной связи.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from kb_agent.storage.state import StateManager


@dataclass
class FeedbackMetrics:
    """Агрегированные метрики обратной связи."""

    total_ratings: int
    average_score: Optional[float]
    score_distribution: dict[int, int]
    total_messages: int
    recent_feedback: list[dict]


class FeedbackMetricsCollector:
    """Сбор метрик обратной связи."""

    def __init__(self, state: StateManager) -> None:
        self.state = state

    def compute(self) -> FeedbackMetrics:
        """Вычислить все метрики."""
        conn = self.state.connect()

        # Все оценки (score BETWEEN 1 AND 5)
        rows = conn.execute(
            """
            SELECT score, comment, created_at
            FROM feedback
            WHERE score BETWEEN 1 AND 5
            ORDER BY created_at DESC
            """
        ).fetchall()
        ratings = [dict(row) for row in rows]

        # Все произвольные сообщения (score = 0)
        msg_rows = conn.execute(
            """
            SELECT score, comment, created_at
            FROM feedback
            WHERE score = 0
            ORDER BY created_at DESC
            """
        ).fetchall()
        messages = [dict(row) for row in msg_rows]

        total_ratings = len(ratings)
        average_score = None
        score_distribution: dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}

        if total_ratings > 0:
            scores = [r["score"] for r in ratings]
            average_score = round(sum(scores) / total_ratings, 2)
            for s in scores:
                score_distribution[s] = score_distribution.get(s, 0) + 1

        return FeedbackMetrics(
            total_ratings=total_ratings,
            average_score=average_score,
            score_distribution=score_distribution,
            total_messages=len(messages),
            recent_feedback=ratings[:10] + messages[:10],
        )
