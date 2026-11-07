"""Команды инициализации базы знаний (Этап 1).

kb-agent interview init         — вводное интервью и создание user_profile.yaml
kb-agent interview status       — статус профиля
kb-agent interview import-core  — импортировать существующие материалы
kb-agent interview periodic     — ежемесячное периодическое интервью (Этап 5)
"""

from __future__ import annotations

from pathlib import Path

import typer

from kb_agent.cli.output import output_json as _output_json
from kb_agent.config import load_config
from kb_agent.core.import_core import run_import_core
from kb_agent.core.interview import InterviewManager
from kb_agent.core.prompts import ensure_prompt_seeded
from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger

app = typer.Typer(help="Интервью и импорт ядра материалов")

logger = setup_logger("kb_agent.interview")


@app.command("init")
def interview_init() -> None:
    config = load_config()
    interview = InterviewManager(config)

    if interview.profile_exists():
        typer.echo(f"Профиль уже существует: {interview.profile_path}")
        if not typer.confirm("Перезаписать существующий профиль?"):
            raise typer.Exit(code=0)

    profile = interview.run_init_interview()
    saved_path = interview.save_profile(profile)
    typer.echo(f"\nПрофиль сохранён: {saved_path}")

    # Сеем базовые версии промптов в prompt_history, чтобы работали
    # откат промпта (self-improve rollback) и MCP-инструмент kb_revert_prompt.
    state = StateManager(config.db_path)
    try:
        state.init_db()
        ensure_prompt_seeded(config, state)
    finally:
        state.close()

    typer.echo("База знаний готова к наполнению (Этап 2).")


@app.command("status")
def interview_status(
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    config = load_config()
    interview = InterviewManager(config)

    state = StateManager(config.db_path)
    try:
        version = state.init_db()
        stats = state.stats()

        if json_output:
            _output_json(
                {
                    "profile_exists": interview.profile_exists(),
                    "profile_path": str(interview.profile_path),
                    "db_path": str(config.db_path),
                    "schema_version": version,
                    "stats": stats,
                }
            )
            return

        if interview.profile_exists():
            typer.echo(f"Профиль: {interview.profile_path} (существует)")
        else:
            typer.echo("Профиль: не создан. Запустите 'kb-agent interview init'.")

        typer.echo(f"База данных: {config.db_path} (схема v{version})")
        typer.echo(f"Элементов в очереди: {stats.get('raw_queue', 0)}")
        typer.echo(f"Обратная связь: {stats.get('feedback', 0)}")
    finally:
        state.close()


@app.command("import-core")
def import_core(
    path: str = typer.Argument(..., help="Путь к каталогу с существующими материалами"),
    dry_run: bool = typer.Option(True, "--dry-run/--apply", help="Пробный запуск без записи"),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        report = run_import_core(config, state, Path(path), dry_run=dry_run)

        if json_output:
            _output_json(
                {
                    "total_files": report.total_files,
                    "imported": report.imported,
                    "errors": report.errors,
                    "details": report.details,
                    "dry_run": dry_run,
                }
            )
        else:
            typer.echo(f"Всего файлов: {report.total_files}")
            typer.echo(f"Импортировано: {report.imported}")
            typer.echo(f"Пропущено (уже было): {report.skipped}")
            typer.echo(f"Ошибок: {report.errors}")
            for line in report.details:
                typer.echo(f"  - {line}")
    finally:
        state.close()


@app.command("periodic")
def interview_periodic() -> None:
    """Ежемесячное периодическое интервью (Этап 5).

    Спрашивает об изменениях целей, задач, источников и качестве заметок,
    обновляет user_profile.yaml.
    """
    config = load_config()
    interview = InterviewManager(config)

    if not interview.profile_exists():
        typer.echo(
            "Профиль не создан. Сначала запустите 'kb-agent interview init'.",
            err=True,
        )
        raise typer.Exit(code=1)

    profile = interview.load_profile() or {}
    typer.echo(f"Текущий профиль: {interview.profile_path}")
    typer.echo("Ответьте на несколько вопросов для обновления профиля.\n")

    updates: dict = {}

    goal = interview.ask(
        f"Изменилась ли цель базы знаний? [{profile.get('goal', '')}]",
        default=profile.get("goal", ""),
    )
    if goal.strip():
        updates["goal"] = goal.strip()

    tasks = interview.ask(
        f"Какую задачу она должна решать сейчас? [{profile.get('tasks', '')}]",
        default=profile.get("tasks", ""),
    )
    if tasks.strip():
        updates["tasks"] = tasks.strip()

    topics = interview.ask(
        f"Какие темы вас интересуют? [{', '.join(profile.get('topics', []))}]",
        default=", ".join(profile.get("topics", [])),
    )
    if topics.strip():
        updates["topics"] = [t.strip() for t in topics.split(",") if t.strip()]

    sources = interview.ask(
        f"Какие источники используются? [{', '.join(profile.get('sources', []))}]",
        default=", ".join(profile.get("sources", [])),
    )
    if sources.strip():
        updates["sources"] = [s.strip() for s in sources.replace(";", ",").split(",") if s.strip()]

    frequency = interview.ask(
        f"С какой частотой собирать информацию? [{profile.get('frequency', '')}]",
        default=profile.get("frequency", ""),
    )
    if frequency.strip():
        updates["frequency"] = frequency.strip()

    system = interview.ask(
        f"В какой системе вы работаете? [{profile.get('system', 'Obsidian')}]",
        default=profile.get("system", "Obsidian"),
    )
    if system.strip():
        updates["system"] = system.strip()

    if not updates:
        typer.echo("Изменений не введено. Профиль не обновлён.")
        return

    profile.update(updates)
    saved_path = interview.save_profile(profile)
    typer.echo(f"\nПрофиль обновлён: {saved_path}")
    typer.echo(
        "Запустите 'kb-agent self-improve' для просмотра предложений по промптам и источникам."
    )
