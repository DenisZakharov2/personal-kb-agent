"""Двигатель инсайтов базы знаний (Этап 3).

Выявляет повторяющиеся темы, связи между заметками и пробелы в покрытии.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from kb_agent.config import AppConfig
from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.core.insights")


@dataclass
class Insight:
    """Один инсайт с доказательствами."""

    statement: str
    evidence_note_ids: list[str]
    link_type: str  # repeating_theme / contradiction / gap / connection
    confidence: str  # high / medium / low
    benefit: str


@dataclass
class InsightsResponse:
    """Список инсайтов."""

    insights: list[Insight] = field(default_factory=list)


class InsightsEngine:
    """Анализирует накопленные заметки и выявляет закономерности."""

    def __init__(self, config: AppConfig, state: StateManager) -> None:
        self.config = config
        self.state = state

    def get_insights(self, limit: int = 5) -> InsightsResponse:
        """Возвращает инсайты: повторяющиеся темы, связи, пробелы."""
        insights: list[Insight] = []

        # 1. Повторяющиеся темы — ищем заметки с одинаковыми тегами
        theme_insights = self._find_repeating_themes()
        insights.extend(theme_insights)

        # 2. Конфликтующие темы — ищем заметки с противоположной окраской
        conflict_insights = self._find_contradictions()
        insights.extend(conflict_insights)

        # 3. Связи между заметками — существующие note_links
        link_insights = self._find_existing_links()
        insights.extend(link_insights)

        # Ограничиваем количество
        insights = insights[:limit]

        return InsightsResponse(insights=insights)

    def _find_repeating_themes(self) -> list[Insight]:
        """Находит темы, которые встречаются в нескольких заметках."""
        insights: list[Insight] = []
        # Простой подход: ищем теги, которые повторяются
        # В реальности здесь можно сделать SQL-запрос по notes_fts или по тегам
        return insights

    def _find_contradictions(self) -> list[Insight]:
        """Находит противоречивые утверждения между заметками."""
        insights: list[Insight] = []
        # Требует семантического анализа — пока заглушка
        return insights

    def _find_existing_links(self) -> list[Insight]:
        """Находит существующие связи между заметками в note_links."""
        import sqlite3

        insights: list[Insight] = []
        conn = self.state.connect()
        rows = conn.execute(
            """
            SELECT source_note_id, target_note_id, link_type
            FROM note_links
            LIMIT 10
            """
        ).fetchall()
        for row in rows:
            insights.append(
                Insight(
                    statement=f"Заметка {row['source_note_id']} связана с {row['target_note_id']} ({row['link_type']})",
                    evidence_note_ids=[row["source_note_id"], row["target_note_id"]],
                    link_type="connection",
                    confidence="high",
                    benefit="Помогает увидеть связь между материалами",
                )
            )
        return insights
