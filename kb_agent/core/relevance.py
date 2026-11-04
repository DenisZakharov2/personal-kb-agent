"""Relevance Gate (Этап 5).

Оценка сырого материала по user_profile.yaml (соответствие целям,
отсечение рекламы/воды). Пропуск помечается как 'skipped' с причиной.

Scoring (P2-03):
- Оценивается ``лучшее совпадение``: для каждой темы профиля считается
  доля её токенов, найденных в тексте (0.0 - 1.0), и берётся максимум.
  Так одна сильная совпавшая тема даёт высокий скор независимо от
  общего числа тем в профиле (ранее считалась доля всех тем, и при 4+
  темах одно совпадение давало < 0.3 и материал отбрасывался).
- goal и tasks из профиля тоже участвуют в оценке.
- Порог настраивается через ``profile['relevance_threshold']``
  (по умолчанию 0.3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_WORD_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)

DEFAULT_THRESHOLD = 0.3


@dataclass
class RelevanceResult:
    """Результат проверки релевантности."""

    relevant: bool
    reason: str
    score: float  # 0.0 - 1.0


class RelevanceGate:
    """Оценивает сырой материал на релевантность профилю пользователя."""

    def __init__(self, profile: dict) -> None:
        if not isinstance(profile, dict):
            raise TypeError("профиль должен быть словарём")

        self.profile = profile

        topics = profile.get("topics", [])
        if not isinstance(topics, list):
            raise TypeError("profile['topics'] должен быть списком")
        self.topics = [str(t).lower().strip() for t in topics if str(t).strip()]

        self.goal = str(profile.get("goal", "")).lower().strip()
        self.tasks = str(profile.get("tasks", "")).lower().strip()

        try:
            self.threshold = float(
                profile.get("relevance_threshold", DEFAULT_THRESHOLD)
            )
        except (TypeError, ValueError):
            self.threshold = DEFAULT_THRESHOLD
        self.threshold = max(0.0, min(1.0, self.threshold))

    # ---- helpers ----

    @staticmethod
    def _tokens(text: str) -> set[str]:
        """Нормализует текст во множество токенов (слова/цифры, без пунктуации)."""
        return set(_WORD_RE.findall(text or ""))

    @staticmethod
    def _coverage(query_tokens: set[str], text_tokens: set[str]) -> float:
        """Доля токенов запроса, найденных в тексте (0.0 - 1.0)."""
        if not query_tokens:
            return 0.0
        return len(query_tokens & text_tokens) / len(query_tokens)

    def _topic_score(self, text_tokens: set[str]) -> float:
        """Лучшее покрытие любой темы профиля токенами текста (0.0 - 1.0)."""
        if not self.topics:
            return 0.0
        return max(
            (self._coverage(self._tokens(t), text_tokens) for t in self.topics),
            default=0.0,
        )

    def _goal_task_score(self, text_tokens: set[str]) -> float:
        """Лучшее покрытие goal/tasks профиля токенами текста (0.0 - 1.0)."""
        parts = [p for p in (self.goal, self.tasks) if p]
        if not parts:
            return 0.0
        return max(
            (self._coverage(self._tokens(p), text_tokens) for p in parts),
            default=0.0,
        )

    # ---- public API ----

    def check(self, raw_text: str, title: str = "") -> RelevanceResult:
        """Проверяет сырой материал на релевантность.

        Возвращает RelevanceResult с флагом relevant, причиной и score.
        """
        text = (title + " " + raw_text).lower()
        text_tokens = self._tokens(text)

        # 1. Спам-ключевые слова — всегда пропускаем
        spam_keywords = [
            "розыгрыш",
            "giveaway",
            "подпишись на канал",
            "подписаться на",
            "реклама",
            "вложения",
            "заработай",
            "инвестируй",
            "купи",
            "промокод",
            "скидка",
            "розыгрыш приза",
            "бесплатный розыгрыш",
            "скидка до",
            "распродажа",
            "купон",
        ]
        for kw in spam_keywords:
            if kw in text:
                return RelevanceResult(
                    relevant=False,
                    reason=f"спам: '{kw}'",
                    score=0.0,
                )

        # 2. Если профиль не задан — считаем релевантным
        if not self.topics and not self.goal and not self.tasks:
            return RelevanceResult(
                relevant=True,
                reason="профиль не задан, материал принят",
                score=0.5,
            )

        # 3. Лучшее совпадение по темам профиля (не доля всех тем)
        topic_score = self._topic_score(text_tokens)

        # 4. Лучшее совпадение по goal / tasks
        goal_task_score = self._goal_task_score(text_tokens)

        # Итоговый скор — максимум из тематического и goal/tasks.
        score = max(topic_score, goal_task_score)

        # 5. Порог релевантности (настраиваемый)
        if score >= self.threshold:
            return RelevanceResult(
                relevant=True,
                reason=f"соответствует профилю ({score:.0%})",
                score=score,
            )
        elif score >= self.threshold / 2:
            return RelevanceResult(
                relevant=False,
                reason=f"слабое соответствие профилю ({score:.0%})",
                score=score,
            )
        else:
            return RelevanceResult(
                relevant=False,
                reason="не соответствует профилю пользователя",
                score=score,
            )
