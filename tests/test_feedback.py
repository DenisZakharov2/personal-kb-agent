"""Тесты обратной связи (Этап 5): rate, tell, metrics, list."""

from __future__ import annotations

from typer.testing import CliRunner

from kb_agent.cli.main import app
import kb_agent.cli.commands.feedback as feedback_mod


def _invoke(config, monkeypatch, args, json_output=True):
    monkeypatch.setattr(feedback_mod, "load_config", lambda: config)
    runner = CliRunner()
    args_with_json = list(args) + (["--json"] if json_output else [])
    return runner.invoke(app, args_with_json, prog_name="kb-agent")


class TestFeedbackRate:
    """Оценка заметки (kb-agent feedback rate)."""

    def test_rate_note_saves_feedback(self, config, state, monkeypatch):
        """Оценка 1-5 сохраняется и возвращает feedback_id."""
        result = _invoke(
            config, monkeypatch, ["feedback", "rate", "note-123", "5", "--comment", "Отлично"]
        )
        assert result.exit_code == 0, result.output
        data = __import__("json").loads(result.output)
        assert data["action"] == "rate"
        assert data["note_id"] == "note-123"
        assert data["score"] == 5
        assert data["comment"] == "Отлично"
        assert data["status"] == "saved"
        assert "feedback_id" in data

    def test_rate_invalid_score_rejected(self, config, state, monkeypatch):
        """Оценка вне диапазона 1-5 отклоняется с кодом 1."""
        result = _invoke(
            config, monkeypatch, ["feedback", "rate", "note-123", "0"]
        )
        assert result.exit_code == 1
        data = __import__("json").loads(result.output)
        assert "error" in data
        assert "от 1 до 5" in data["error"]

        result = _invoke(
            config, monkeypatch, ["feedback", "rate", "note-123", "6"]
        )
        assert result.exit_code == 1
        data = __import__("json").loads(result.output)
        assert "error" in data

    def test_rate_without_comment(self, config, state, monkeypatch):
        """Оценка без комментария сохраняется."""
        result = _invoke(
            config, monkeypatch, ["feedback", "rate", "note-456", "3"]
        )
        assert result.exit_code == 0
        data = __import__("json").loads(result.output)
        assert data["comment"] == ""
        assert data["score"] == 3


class TestFeedbackTell:
    """Произвольное сообщение (kb-agent feedback tell)."""

    def test_tell_saves_message(self, config, state, monkeypatch):
        """Сообщение сохраняется со score=0 и note_id=__global__."""
        result = _invoke(
            config, monkeypatch, ["feedback", "tell", "Нужно больше примеров кода"]
        )
        assert result.exit_code == 0, result.output
        data = __import__("json").loads(result.output)
        assert data["action"] == "tell"
        assert data["message"] == "Нужно больше примеров кода"
        assert data["status"] == "saved"
        assert "feedback_id" in data


class TestFeedbackMetrics:
    """Агрегированные метрики (kb-agent feedback metrics)."""

    def test_metrics_empty_initially(self, config, state, monkeypatch):
        """Без отзывов метрики показывают нули."""
        result = _invoke(config, monkeypatch, ["feedback", "metrics"])
        assert result.exit_code == 0
        data = __import__("json").loads(result.output)
        assert data["total_ratings"] == 0
        assert data["average_score"] is None
        assert data["total_messages"] == 0
        assert data["recent_feedback"] == []

    def test_metrics_with_ratings(self, config, state, monkeypatch):
        """После оценок метрики отражают среднее и распределение."""
        import json as _json

        # Добавляем несколько оценок напрямую через collector
        from kb_agent.feedback.collector import FeedbackCollector

        collector = FeedbackCollector(state)
        collector.rate("note-1", 5, "Отлично")
        collector.rate("note-2", 4, "Хорошо")
        collector.rate("note-3", 5, "Супер")
        collector.tell("Произвольное сообщение")

        result = _invoke(config, monkeypatch, ["feedback", "metrics"])
        assert result.exit_code == 0
        data = _json.loads(result.output)
        assert data["total_ratings"] == 3
        assert data["average_score"] == 4.67
        assert data["total_messages"] == 1
        assert data["score_distribution"]["5"] == 2
        assert data["score_distribution"]["4"] == 1
        assert len(data["recent_feedback"]) == 4


class TestFeedbackList:
    """Список отзывов по заметке (kb-agent feedback list)."""

    def test_list_empty_for_note(self, config, state, monkeypatch):
        """По несуществующей заметке отзывов нет."""
        result = _invoke(
            config, monkeypatch, ["feedback", "list", "несуществующая-заметка"]
        )
        assert result.exit_code == 0
        data = __import__("json").loads(result.output)
        assert data["note_id"] == "несуществующая-заметка"
        assert data["count"] == 0
        assert data["feedback"] == []

    def test_list_returns_ratings_for_note(self, config, state, monkeypatch):
        """Возвращаются все отзывы по указанной заметке."""
        import json as _json

        from kb_agent.feedback.collector import FeedbackCollector

        collector = FeedbackCollector(state)
        collector.rate("note-abc", 5, "Первый отзыв")
        collector.rate("note-abc", 3, "Второй отзыв")
        collector.rate("note-xyz", 1, "Отзыв по другой заметке")

        result = _invoke(
            config, monkeypatch, ["feedback", "list", "note-abc"]
        )
        assert result.exit_code == 0
        data = _json.loads(result.output)
        assert data["note_id"] == "note-abc"
        assert data["count"] == 2
        scores = {f["score"] for f in data["feedback"]}
        assert scores == {5, 3}
