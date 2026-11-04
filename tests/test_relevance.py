"""Тесты relevance gate (P2-03).

Scoring оценивает лучшее совпадение (не долю всех тем), goal/tasks
участвуют в оценке, порог настраиваемый, профиль валидируется.
"""

from __future__ import annotations

import pytest

from kb_agent.core.relevance import RelevanceGate


def _gate(profile: dict) -> RelevanceGate:
    return RelevanceGate(profile)


class TestRelevanceGateBestMatch:
    """P2-03: лучшее совпадение проходит независимо от числа тем."""

    def test_one_strong_match_passes_regardless_of_topic_count(self):
        """Одна совпавшая тема из многих должна быть релевантной."""
        profile = {
            "topics": ["ИИ", "программирование", "автоматизация", "нейросети", "python"],
            "goal": "",
            "tasks": "",
        }
        gate = _gate(profile)

        result = gate.check(raw_text="Новости про ИИ и нейросети", title="")

        assert result.relevant is True
        assert result.score == 1.0  # тема «ИИ» полностью покрыта текстом

    def test_one_match_out_of_four_topics_passes(self):
        """Одна тема из четырёх — скор 1.0, проходит порог 0.3."""
        profile = {
            "topics": ["ИИ", "программирование", "автоматизация", "нейросети"],
            "goal": "",
            "tasks": "",
        }
        gate = _gate(profile)

        result = gate.check(raw_text="Новости про ИИ", title="")

        assert result.relevant is True
        assert result.score == 1.0
        assert "соответствует профилю" in result.reason

    def test_partial_multiword_topic_gives_partial_score(self):
        """Многословная тема покрыта частично — скор пропорционален."""
        profile = {
            "topics": ["искусственный интеллект"],
            "goal": "",
            "tasks": "",
        }
        gate = _gate(profile)

        # В тексте есть только «интеллект» из двух токенов темы → 0.5 >= 0.3
        result = gate.check(raw_text="Разработка интеллект-систем", title="")

        assert result.relevant is True
        assert result.score == 0.5

    def test_no_match_with_many_topics_fails(self):
        """Если ни одна тема не совпала — не релевантно, независимо от числа тем."""
        profile = {
            "topics": ["ИИ", "программирование", "автоматизация", "нейросети", "python", "go", "rust"],
            "goal": "",
            "tasks": "",
        }
        gate = _gate(profile)

        result = gate.check(raw_text="Купить хлеб и молоко", title="")

        assert result.relevant is False
        assert result.score == 0.0

    def test_goal_and_tasks_considered(self):
        """goal и tasks участвуют в оценке даже без тем."""
        profile = {
            "topics": [],
            "goal": "изучать нейросети",
            "tasks": "построить модель",
        }
        gate = _gate(profile)

        result = gate.check(raw_text="Нейросети и глубокое обучение", title="")

        # goal: токены {изучать, нейросети}, в тексте есть нейросети → 0.5
        assert result.relevant is True  # 0.5 >= 0.3
        assert result.score == 0.5

    def test_goal_alone_matches(self):
        """Совпадение с goal без тем даёт релевантность."""
        profile = {
            "topics": [],
            "goal": "автоматизация бизнес-процессов",
            "tasks": "",
        }
        gate = _gate(profile)

        result = gate.check(raw_text="Как автоматизировать бизнес", title="")

        # goal tokens: {автоматизация, бизнес, процессы}; в тексте {как, автоматизировать, бизнес}
        # только «бизнес» совпал → 1/3 ≈ 0.33
        assert result.relevant is True  # 0.33 >= 0.3
        assert abs(result.score - (1 / 3)) < 1e-9


class TestRelevanceGateThreshold:
    """P2-03: порог настраиваемый."""

    def test_custom_threshold(self):
        """relevance_threshold из профиля управляет порогом."""
        profile = {
            "topics": ["ИИ"],
            "goal": "",
            "tasks": "",
            "relevance_threshold": 0.8,
        }
        gate = _gate(profile)

        result = gate.check(raw_text="ИИ", title="")
        assert result.relevant is True  # скор 1.0 >= 0.8

    def test_invalid_threshold_falls_back_to_default(self):
        """Некорректный порог — значение по умолчанию."""
        profile = {
            "topics": ["ИИ"],
            "goal": "",
            "tasks": "",
            "relevance_threshold": "нечисло",
        }
        gate = _gate(profile)
        assert gate.threshold == 0.3

    def test_threshold_clamped_to_range(self):
        """Порог обрезается к [0, 1]."""
        profile = {
            "topics": ["ИИ"],
            "goal": "",
            "tasks": "",
            "relevance_threshold": 2.5,
        }
        gate = _gate(profile)
        assert gate.threshold == 1.0

        profile["relevance_threshold"] = -1.0
        gate = _gate(profile)
        assert gate.threshold == 0.0


class TestRelevanceGateValidation:
    """P2-03: валидация типов профиля."""

    def test_profile_must_be_dict(self):
        with pytest.raises(TypeError, match="профиль должен быть словарём"):
            RelevanceGate([])  # type: ignore[arg-type]

    def test_topics_must_be_list(self):
        with pytest.raises(TypeError, match="topics.*списком"):
            RelevanceGate({"topics": "ИИ"})  # type: ignore[arg-type]


class TestRelevanceGateSpamAndEmpty:
    """Поведение спама и пустого профиля сохранилось."""

    def test_spam_always_skipped(self):
        profile = {"topics": ["ИИ"], "goal": "", "tasks": ""}
        gate = _gate(profile)

        result = gate.check(raw_text="Розыгрыш приза!", title="")

        assert result.relevant is False
        assert result.score == 0.0
        assert "спам" in result.reason

    def test_empty_profile_accepted(self):
        profile = {"topics": [], "goal": "", "tasks": ""}
        gate = _gate(profile)

        result = gate.check(raw_text="Любой текст", title="")

        assert result.relevant is True
        assert result.score == 0.5
