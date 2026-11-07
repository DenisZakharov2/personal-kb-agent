"""Двигатель инсайтов базы знаний (Этап 3).

Выявляет повторяющиеся темы, связи между заметками и пробелы в покрытии тем
из профиля пользователя. Поиск семантических противоречий требует LLM-анализа
и пока не реализован (см. `_find_contradictions`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

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
        """Возвращает инсайты: повторяющиеся темы, пробелы, связи."""
        insights: list[Insight] = []
        insights.extend(self._find_repeating_themes())
        insights.extend(self._find_gaps())
        insights.extend(self._find_contradictions())
        insights.extend(self._find_existing_links())
        return InsightsResponse(insights=insights[:limit])

    # ---- Вспомогательное ----

    def _tag_index(self) -> dict[str, list[str]]:
        """tag -> список note_id."""
        conn = self.state.connect()
        rows = conn.execute("SELECT note_id, tags FROM notes_fts").fetchall()
        index: dict[str, list[str]] = {}
        for row in rows:
            for tag in (row["tags"] or "").split(","):
                tag = tag.strip()
                if tag:
                    index.setdefault(tag.lower(), []).append(row["note_id"])
        return index

    def _profile_topics(self) -> list[str]:
        """Темы из профиля пользователя (секция user)."""
        path = Path(self.config.profile_path)
        if not path.exists():
            return []
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось прочитать профиль: %s", exc)
            return []
        user = data.get("user", {}) if isinstance(data, dict) else {}
        topics = user.get("topics") or []
        if isinstance(topics, str):
            topics = [t.strip() for t in topics.split(",") if t.strip()]
        return [str(topic).strip() for topic in topics if str(topic).strip()]

    # ---- Типы инсайтов ----

    def _find_repeating_themes(self, max_insights: int = 10) -> list[Insight]:
        """Находит теги, которые встречаются в нескольких заметках."""
        insights: list[Insight] = []
        for tag, note_ids in sorted(self._tag_index().items(), key=lambda kv: -len(kv[1])):
            if len(note_ids) < 2:
                # Список отсортирован по убыванию: дальше только одиночные теги
                break
            insights.append(
                Insight(
                    statement=f"Тема «{tag}» повторяется в заметках: {len(note_ids)}",
                    evidence_note_ids=note_ids[:10],
                    link_type="repeating_theme",
                    confidence="high" if len(note_ids) >= 3 else "medium",
                    benefit="Материалы по теме можно собрать в подборку или цикл",
                )
            )
            if len(insights) >= max_insights:
                break
        return insights

    def _find_gaps(self) -> list[Insight]:
        """Темы из профиля, по которым в базе ещё нет материалов."""
        topics = self._profile_topics()
        if not topics:
            return []

        conn = self.state.connect()
        rows = conn.execute("SELECT title, tags, topic, content FROM notes_fts").fetchall()
        haystack = " ".join(
            " ".join(str(row[key] or "") for key in ("title", "tags", "topic", "content"))
            for row in rows
        ).lower()

        insights: list[Insight] = []
        for topic in topics:
            if topic.lower() in haystack:
                continue
            insights.append(
                Insight(
                    statement=f"По теме «{topic}» из профиля ещё нет материалов",
                    evidence_note_ids=[],
                    link_type="gap",
                    confidence="high",
                    benefit="Подсказывает, какие источники стоит добавить",
                )
            )
        return insights

    def _find_contradictions(self) -> list[Insight]:
        """Противоречия между заметками (требует семантического анализа)."""
        logger.debug("Поиск противоречий не реализован: требуется LLM-анализ заметок")
        return []

    def _find_existing_links(self) -> list[Insight]:
        """Находит существующие связи между заметками в note_links."""
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
