"""Тесты eval-набора (раздел 9 аудита: отдельный eval-набор перед активацией промптов)."""

from __future__ import annotations

import json
from pathlib import Path

from kb_agent.core.self_improvement import SelfImprovementEngine


class TestEvalSet:
    def test_eval_set_json_is_valid(self):
        """Файл tests/eval_set.json валиден и содержит вопросы с критериями."""
        path = Path("tests/eval_set.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        questions = data.get("questions") or data.get("eval_set") or []
        assert isinstance(questions, list) and len(questions) >= 1
        for q in questions:
            assert "question" in q
            assert "criteria" in q
            assert "forbidden_hallucinations" in q
            assert "expected_note_ids" in q

    def test_eval_engine_passes_with_notes(self, state):
        """Eval-проверка возвращает True, если достаточно совпадений."""
        engine = SelfImprovementEngine(state)
        state.index_note(
            note_id="note-auto-1",
            title="Автоматизация",
            tags="автоматизация",
            topic="Инструменты",
            content="В заметке описаны инструменты автоматизации n8n и скрипты.",
            source_id="tg_test",
            external_id="ext_auto",
            canonical_url="https://t.me/s/channel/42",
        )
        state.index_note(
            note_id="note-ai-1",
            title="ИИ",
            tags="нейросети",
            topic="ИИ",
            content="Глубокое обучение и нейросети меняют подход к разработке.",
            source_id="tg_test",
            external_id="ext_ai",
            canonical_url="https://t.me/s/channel/43",
        )
        result = engine._run_eval_check()
        assert result is True

    def test_eval_engine_no_notes_passes(self, state):
        """Если заметок ещё нет, eval-проверка пропускается (не блокирует)."""
        engine = SelfImprovementEngine(state)
        assert engine._run_eval_check() is True

    def test_eval_engine_fails_when_expected_missing(self, state):
        """Если ожидаемая заметка не найдена, eval не проходит."""
        state.index_note(
            note_id="note-other",
            title="Другая",
            tags="прочее",
            topic="Прочее",
            content="Не Related контент.",
        )
        engine = SelfImprovementEngine(state)
        # Нет заметок note-auto-1 / note-ai-1 → ожидаемые не найдены
        assert engine._run_eval_check() is False
