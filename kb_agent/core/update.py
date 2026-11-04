"""Актуализация базы знаний (Этап 3).

Поиск дублей, конфликтов и предложение слияния заметок.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kb_agent.config import AppConfig
from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.core.update")


@dataclass
class DuplicatePair:
    """Пара похожих заметок, кандидаты на слияние."""

    note_id_1: str
    title_1: str
    note_id_2: str
    title_2: str
    similarity_reason: str


@dataclass
class UpdateReport:
    """Отчёт об актуализации базы знаний."""

    duplicates: list[DuplicatePair] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


class KnowledgeUpdater:
    """Сравнивает заметки и предлагает слияние дублей или обновление."""

    def __init__(self, config: AppConfig, state: StateManager) -> None:
        self.config = config
        self.state = state

    def check_duplicates(self, limit: int = 10) -> UpdateReport:
        """Ищет возможные дубликаты среди заметок."""
        report = UpdateReport()

        # Используем FTS5 поиск по самому себе — ищем заметки с похожим содержимым
        # В простой реализации: ищем пары с общими тегами и похожими заголовками
        conn = self.state.connect()
        rows = conn.execute(
            """
            SELECT
                n1.note_id AS note_id_1,
                n1.title AS title_1,
                n2.note_id AS note_id_2,
                n2.title AS title_2
            FROM notes_fts n1
            JOIN notes_fts n2 ON n1.note_id < n2.note_id
            WHERE n1.tags = n2.tags AND n1.tags != ''
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        for row in rows:
            report.duplicates.append(
                DuplicatePair(
                    note_id_1=row["note_id_1"],
                    title_1=row["title_1"] or "Без названия",
                    note_id_2=row["note_id_2"],
                    title_2=row["title_2"] or "Без названия",
                    similarity_reason="Общие теги",
                )
            )

        if report.duplicates:
            report.suggestions.append(
                "Найдены возможные дубликаты. Рекомендуется проверить и объединить схожие заметки."
            )
        else:
            report.suggestions.append("Дубликатов не найдено.")

        return report

    def check_conflicts(self) -> UpdateReport:
        """Ищет противоречия между заметками (заглушка для будущего семантического анализа)."""
        report = UpdateReport()
        report.conflicts.append(
            "Семантический поиск противоречий будет доступен в следующей версии."
        )
        return report
