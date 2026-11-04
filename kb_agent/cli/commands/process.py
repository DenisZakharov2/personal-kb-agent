"""Команды конвейера наполнения базы знаний (Этап 2).

kb-agent process run   — запустить сбор и суммаризацию
kb-agent process status — показать статус базы данных
kb-agent process digest — запустить конвейер и вывести дайджест
"""

from __future__ import annotations

import json
import typer

from kb_agent.config import load_config
from kb_agent.processing.pipeline import Pipeline, run_digest
from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger

app = typer.Typer(help="Конвейер наполнения базы знаний")

logger = setup_logger("kb_agent.process")


def _output_json(data: dict) -> None:
    """Выводит данные в формате JSON."""
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


@app.command("run")
def process_run(
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Запустить конвейер наполнения (сбор + суммаризация + запись в vault)."""
    config = load_config()
    pipeline = Pipeline(config)
    try:
        result = pipeline.run()

        if json_output:
            _output_json(result)
        else:
            typer.echo("\n=== Результат запуска конвейера ===")
            coll = result["collection"]
            tg = coll["telegram"]
            yt = coll["youtube"]
            typer.echo(f"Telegram: новых {tg['new']}, спама {tg['spam']}, уже было {tg['existing']}")
            typer.echo(f"YouTube: новых {yt['new']}, пропущено {yt['skipped']}, с субтитрами {yt['with_subtitles']}")
            proc = result["processing"]
            typer.echo(f"Обработано в очереди: {proc['processed']}")
            typer.echo(f"Создано заметок: {proc['created']}")
            typer.echo(f"Пропущено (relevance): {proc.get('relevance_skipped', 0)}")
            typer.echo(f"Пропущено (другое): {proc['skipped']}")
            typer.echo(f"Ошибок: {proc['failed']}")
            if result.get("catalog"):
                typer.echo(f"Каталог: {result['catalog']}")
            if result.get("vault_digest"):
                typer.echo(f"Дайджест: {result['vault_digest']}")
    finally:
        pipeline.close()


@app.command("digest")
def process_digest(
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Запустить конвейер и вывести полный дайджест в формате Markdown."""
    config = load_config()
    text = run_digest(config)

    if json_output:
        _output_json({"digest": text})
    else:
        typer.echo(text)


@app.command("status")
def process_status(
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Показать статус базы данных."""
    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        stats = state.stats()

        if json_output:
            _output_json(stats)
        else:
            typer.echo("Статус конвейера:")
            for key, value in stats.items():
                typer.echo(f"  {key}: {value}")
    finally:
        state.close()
