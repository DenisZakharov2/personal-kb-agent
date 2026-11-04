"""Relevance Gate (Этап 5).

Оценка сырого материала по user_profile.yaml (соответствие целям,
отсечение рекламы/воды). Пропуск помечается как 'skipped' с причиной.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RelevanceResult:
    """Результат проверки релевантности."""

    relevant: bool
    reason: str
    score: float  # 0.0 - 1.0


class RelevanceGate:
    """Оценивает сырой материал на релевантность профилю пользователя."""

    def __init__(self, profile: dict) -> None:
        self.profile = profile
        self.topics = [t.lower() for t in profile.get("topics", [])]
        self.goal = profile.get("goal", "").lower()
        self.tasks = profile.get("tasks", "").lower()

    def check(self, raw_text: str, title: str = "") -> RelevanceResult:
        """Проверяет сырой материал на релевантность.

        Возвращает RelevantResult с флагом relevant, причиной и скором.
        """
        text = (title + " " + raw_text).lower()

        # 1. Спам-ключевые слова — всегда пропускаем
        spam_keywords = [
            "розыгрыш", "giveaway", "подпишись на канал", "подписаться на",
            "реклама", "вложения", "заработай", "инвестируй", "купи",
            "промокод", "скидка", "розыгрыш приза", "бесплатный розыгрыш",
            "скидка до", "распродажа", "купон",
        ]
        for kw in spam_keywords:
            if kw in text:
                return RelevanceResult(
                    relevant=False,
                    reason=f"спам: '{kw}'",
                    score=0.0,
                )

        # 2. Если профиль не задан — считаем релевантным
        if not self.topics and not self.goal:
            return RelevanceResult(
                relevant=True,
                reason="профиль не задан, материал принят",
                score=0.5,
            )

        # 3. Подсчёт совпадений по темам из профиля
        topic_matches = sum(1 for t in self.topics if t and t in text)
        topic_score = topic_matches / max(len(self.topics), 1)

        # 4. Порог релевантности
        if topic_score >= 0.3:
            return RelevanceResult(
                relevant=True,
                reason=f"тема совпала с профилем ({topic_score:.0%})",
                score=topic_score,
            )
        elif topic_score >= 0.1:
            return RelevanceResult(
                relevant=False,
                reason=f"слабое совпадение темы ({topic_score:.0%})",
                score=topic_score,
            )
        else:
            return RelevanceResult(
                relevant=False,
                reason="тема не соответствует профилю пользователя",
                score=topic_score,
            )
