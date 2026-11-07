"""Сбор обратной связи пользователя по заметкам и ответам (Этап 5).

Позволяет пользователю оценивать заметки (оценка 1-5 + комментарий)
и оставлять произвольные сообщения обратной связи.
"""

from __future__ import annotations

from dataclasses import dataclass

from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.feedback.collector")


@dataclass
class FeedbackEntry:
    """Запись обратной связи."""

    note_id: str
    score: int
    comment: str
    created_at: str


class FeedbackCollector:
    """Сбор и чтение обратной связи."""

    def __init__(self, state: StateManager) -> None:
        self.state = state

    def rate(self, note_id: str, score: int, comment: str = "") -> int:
        """Оценить заметку.

        :param note_id: ID заметки
        :param score: оценка от 1 до 5
        :param comment: произвольный комментарий
        :returns: id созданной записи
        """
        if not (1 <= score <= 5):
            raise ValueError("Оценка должна быть от 1 до 5")
        feedback_id = self.state.add_feedback(note_id, score, comment)
        logger.info(
            "Обратная связь: note_id=%s score=%s comment=%s",
            note_id,
            score,
            comment,
        )
        return feedback_id

    def tell(self, message: str) -> int:
        """Оставить произвольное сообщение обратной связи (без привязки к заметке).

        Сохраняется как запись с note_id='__global__' и score=0.
        """
        feedback_id = self.state.add_feedback("__global__", 0, message)
        logger.info("Сообщение обратной связи: %s", message)
        return feedback_id

    def get_feedback_for_note(self, note_id: str) -> list[FeedbackEntry]:
        """Получить все оценки и комментарии для заметки."""
        conn = self.state.connect()
        rows = conn.execute(
            """
            SELECT note_id, score, comment, created_at
            FROM feedback
            WHERE note_id = ?
            ORDER BY created_at DESC
            """,
            (note_id,),
        ).fetchall()
        return [
            FeedbackEntry(
                note_id=row["note_id"],
                score=row["score"],
                comment=row["comment"] or "",
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def get_all_feedback(self) -> list[FeedbackEntry]:
        """Получить всю обратную связь."""
        conn = self.state.connect()
        rows = conn.execute(
            """
            SELECT note_id, score, comment, created_at
            FROM feedback
            ORDER BY created_at DESC
            """,
        ).fetchall()
        return [
            FeedbackEntry(
                note_id=row["note_id"],
                score=row["score"],
                comment=row["comment"] or "",
                created_at=row["created_at"],
            )
            for row in rows
        ]
