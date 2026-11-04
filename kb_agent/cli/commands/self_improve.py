"""Команды самоулучшения (Этап 5).

kb-agent self-improve          — показать метрики и предложения по улучшению
kb-agent self-improve rollback <prompt> <version> — откатить промпт к версии
"""

from __future__ import annotations

import json
import typer

from kb_agent.config import load_config
from kb_agent.core.self_improvement import SelfImprovementEngine
from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger

app = typer.Typer(
    help="Самоулучшение базы знаний под наблюдением человека (Этап 5)",
    invoke_without_command=True,
)
logger = setup_logger("kb_agent.cli.self_improve")


def _output_json(data: dict) -> None:
    """Выводит данные в формате JSON."""
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


@app.callback()
def self_improve(
    ctx: typer.Context,
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Показать метрики и предложения по улучшению промптов и конфигурации."""
    if ctx.invoked_subcommand is not None:
        return
    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        engine = SelfImprovementEngine(state)
        metrics = engine.collect_feedback_metrics()

        if json_output:
            _output_json(
                {
                    "total_ratings": metrics["total_ratings"],
                    "average_score": metrics["average_score"],
                    "score_distribution": metrics["score_distribution"],
                    "total_messages": metrics["total_messages"],
                    "recent_feedback": metrics["recent_feedback"],
                }
            )
            return

        typer.echo("## Самоулучшение базы знаний")
        typer.echo("")
        typer.echo("### Метрики обратной связи")
        typer.echo(f"Всего оценок: {metrics['total_ratings']}")
        if metrics["average_score"] is not None:
            typer.echo(f"Средняя оценка: {metrics['average_score']}")
            typer.echo("Распределение:")
            for score in sorted(metrics["score_distribution"]):
                count = metrics["score_distribution"][score]
                typer.echo(f"  {score}: {count}")
        else:
            typer.echo("Оценок пока нет.")
        typer.echo(f"Сообщений: {metrics['total_messages']}")
        typer.echo("")

        typer.echo("### Предложения по улучшению")
        typer.echo(
            "Пока нет автоматических предложений. "
            "Используйте 'kb-agent feedback rate/tell' для сбора обратной связи, "
            "затем предложения будут сформированы на основе метрик."
        )
        typer.echo("")
        typer.echo(
            "Доступные действия:\n"
            "  kb-agent self-improve rollback <prompt_name> <version>  — откат промпта к версии\n"
        )
    finally:
        state.close()


@app.command("rollback")
def self_improve_rollback(
    prompt_name: str = typer.Argument(..., help="Имя промпта (например, agent_prompt)"),
    version: int = typer.Argument(..., help="Версия для отката"),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Откатить промпт к указанной версии (требует подтверждения)."""
    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        engine = SelfImprovementEngine(state)
        current = state.get_active_prompt(prompt_name)
        if not current:
            msg = f"Активная версия промпта '{prompt_name}' не найдена."
            if json_output:
                _output_json({"error": msg, "prompt_name": prompt_name})
            else:
                typer.echo(msg, err=True)
            raise typer.Exit(code=1)

        if current["version"] == version:
            msg = f"Промпт '{prompt_name}' уже на версии {version}."
            if json_output:
                _output_json(
                    {
                        "prompt_name": prompt_name,
                        "current_version": current["version"],
                        "target_version": version,
                        "status": "unchanged",
                        "message": msg,
                    }
                )
            else:
                typer.echo(msg)
            return

        typer.echo(
            f"Откатить промпт '{prompt_name}' с версии {current['version']} на версию {version}?"
        )
        if not typer.confirm("Подтвердить откат?"):
            typer.echo("Откат отменён.")
            raise typer.Exit(code=0)

        success = engine.rollback_prompt(prompt_name, version, confirmed_by="user")
        if success:
            result = {
                "prompt_name": prompt_name,
                "from_version": current["version"],
                "to_version": version,
                "status": "rolled_back",
                "message": f"Промпт '{prompt_name}' откатан к версии {version}.",
            }
            if json_output:
                _output_json(result)
            else:
                typer.echo(f"✓ Промпт '{prompt_name}' откатан к версии {version}.")
        else:
            result = {
                "prompt_name": prompt_name,
                "from_version": current["version"],
                "to_version": version,
                "status": "failed",
                "message": f"Не удалось откатить промпт '{prompt_name}' к версии {version}.",
            }
            if json_output:
                _output_json(result)
            else:
                typer.echo(
                    f"✗ Не удалось откатить промпт '{prompt_name}' к версии {version}.",
                    err=True,
                )
            raise typer.Exit(code=1)
    finally:
        state.close()
