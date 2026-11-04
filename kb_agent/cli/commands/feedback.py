"""Команды обратной связи (Этап 5).

kb-agent feedback rate <note_id> <score> [comment]  — оценить заметку
kb-agent feedback tell <message>                     — оставить сообщение
kb-agent feedback metrics                            — показать метрики
kb-agent feedback list <note_id>                     — показать обратную связь по заметке
"""

from __future__ import annotations

import typer

from kb_agent.config import load_config
from kb_agent.feedback.collector import FeedbackCollector
from kb_agent.feedback.metrics import FeedbackMetricsCollector
from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger
from kb_agent.cli.output import output_json as _output_json

app = typer.Typer(help="Обратная связь по заметкам и ответам (Этап 5)")
logger = setup_logger("kb_agent.cli.feedback")


@app.command("rate")
def feedback_rate(
    note_id: str = typer.Argument(..., help="ID заметки"),
    score: int = typer.Argument(..., help="Оценка от 1 до 5"),
    comment: str = typer.Option("", "--comment", "-c", help="Комментарий"),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Оценить заметку по шкале от 1 до 5."""
    if not (1 <= score <= 5):
        msg = "Ошибка: оценка должна быть от 1 до 5."
        if json_output:
            _output_json({"error": msg, "note_id": note_id, "score": score})
        else:
            typer.echo(msg, err=True)
        raise typer.Exit(code=1)

    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        collector = FeedbackCollector(state)
        feedback_id = collector.rate(note_id, score, comment)
        result = {
            "action": "rate",
            "note_id": note_id,
            "score": score,
            "comment": comment,
            "feedback_id": feedback_id,
            "status": "saved",
        }
        if json_output:
            _output_json(result)
        else:
            typer.echo(
                f"✓ Оценка {score}/5 сохранена для заметки {note_id} (ID отзыва: {feedback_id})."
            )
            if comment:
                typer.echo(f"  Комментарий: {comment}")
    finally:
        state.close()


@app.command("tell")
def feedback_tell(
    message: str = typer.Argument(..., help="Текст сообщения обратной связи"),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Оставить произвольное сообщение обратной связи (без привязки к заметке)."""
    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        collector = FeedbackCollector(state)
        feedback_id = collector.tell(message)
        result = {
            "action": "tell",
            "message": message,
            "feedback_id": feedback_id,
            "status": "saved",
        }
        if json_output:
            _output_json(result)
        else:
            typer.echo(
                f"✓ Сообщение обратной связи сохранено (ID отзыва: {feedback_id})."
            )
            typer.echo(f"  Текст: {message}")
    finally:
        state.close()


@app.command("metrics")
def feedback_metrics(
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Показать агрегированные метрики обратной связи."""
    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        collector = FeedbackMetricsCollector(state)
        metrics = collector.compute()

        if json_output:
            _output_json(
                {
                    "total_ratings": metrics.total_ratings,
                    "average_score": metrics.average_score,
                    "score_distribution": metrics.score_distribution,
                    "total_messages": metrics.total_messages,
                    "recent_feedback": metrics.recent_feedback,
                }
            )
        else:
            typer.echo("## Метрики обратной связи")
            typer.echo("")
            typer.echo(f"Всего оценок: {metrics.total_ratings}")
            if metrics.average_score is not None:
                typer.echo(f"Средняя оценка: {metrics.average_score}")
                typer.echo("Распределение оценок:")
                for score in sorted(metrics.score_distribution):
                    count = metrics.score_distribution[score]
                    bar = "#" * count
                    typer.echo(f"  {score}: {bar} ({count})")
            else:
                typer.echo("Оценок пока нет.")
            typer.echo(f"Сообщений обратной связи: {metrics.total_messages}")
            typer.echo("")

            recent = metrics.recent_feedback
            if recent:
                typer.echo("Последние отзывы:")
                for item in recent:
                    prefix = "оценка" if item["score"] and item["score"] > 0 else "сообщение"
                    score_text = f" ({item['score']}/5)" if item["score"] and item["score"] > 0 else ""
                    typer.echo(
                        f"  [{item['created_at']}] {prefix}{score_text}: {item['comment'] or item.get('note_id', '')}"
                    )
    finally:
        state.close()


@app.command("list")
def feedback_list(
    note_id: str = typer.Argument(..., help="ID заметки"),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Показать всю обратную связь по заметке."""
    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        collector = FeedbackCollector(state)
        entries = collector.get_feedback_for_note(note_id)

        if json_output:
            _output_json(
                {
                    "note_id": note_id,
                    "count": len(entries),
                    "feedback": [
                        {
                            "score": e.score,
                            "comment": e.comment,
                            "created_at": e.created_at,
                        }
                        for e in entries
                    ],
                }
            )
            return

        if not entries:
            typer.echo(f"Обратной связи по заметке {note_id} нет.")
            return
        typer.echo(f"Обратная связь по заметке {note_id}:")
        for e in entries:
            score_text = f"{e.score}/5" if e.score and e.score > 0 else "сообщение"
            typer.echo(f"  [{e.created_at}] {score_text}: {e.comment}")
    finally:
        state.close()
