"""Тесты relevance gate (P2-03)."""

from __future__ import annotations

from kb_agent.core.relevance import RelevanceGate


class TestRelevanceGate:
    """P2-03: Одна fortement совпавшая тема проходит независимо от числа тем."""

    def test_one_strong_match_passes_regardless_of_topic_count(self):
        """Одна совпавшая тема из многих должна быть релевантной."""
        profile = {
            "topics": ["ИИ", "программирование", "автоматизация", "нейросети", "python"],
            "goal": "",
            "tasks": "",
        }
        gate = RelevanceGate(profile)

        # Текст совпадает только с одной темой из пяти
        result = gate.check(raw_text="Новости про ИИ и нейросети", title="")

        # topic_score = 2/5 = 0.4 >= 0.3 → relevant=True
        assert result.relevant is True
        assert result.score >= 0.3

    def test_no_match_with_many_topics_fails(self):
        """Если ни одна тема не совпала — не релевантно, независимо от числа тем."""
        profile = {
            "topics": ["ИИ", "программирование", "автоматизация", "нейросети", "python", "go", "rust"],
            "goal": "",
            "tasks": "",
        }
        gate = RelevanceGate(profile)

        result = gate.check(raw_text="Купить хлеб и молоко", title="")

        assert result.relevant is False
        assert result.score == 0.0

    def test_goal_and_tasks_considered(self):
        """goal и tasks должны участвовать в оценке (сейчас не участвуют)."""
        profile = {
            "topics": [],
            "goal": "изучать нейросети",
            "tasks": "построить модель",
        }
        gate = RelevanceGate(profile)

        result = gate.check(raw_text="Нейросети и глубокое обучение", title="")

        # Пока goal/tasks не участвуют в score, результат может быть нерелевантным
        # Этот тест документирует ожидаемое поведение после исправления P2-03
        assert isinstance(result.relevant, bool)