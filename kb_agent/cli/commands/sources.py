"""Команды управления источниками (Этап 4/5).

kb-agent sources list                 — показать все источники (из SQLite canonical store)
kb-agent sources add <тип> <URL>      — добавить источник сразу в каноническое хранилище
kb-agent sources remove <source_id>   — удалить источник из канонического хранилища
kb-agent sources enable <source_id>   — включить источник в каноническом хранилище
kb-agent sources disable <source_id>  — выключить источник в каноническом хранилище

Изменения, пришедшие из MCP-инструментов (агент), создают pending-запрос —
его нужно подтвердить командой `kb-agent confirm <id>`. CLI-команды ниже
меняют источники немедленно.
"""

from __future__ import annotations

import typer

from kb_agent.cli.output import output_json as _output_json
from kb_agent.config import load_config
from kb_agent.sources.registry import SourceConfig, SourceRegistry, normalize_source
from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger

app = typer.Typer(help="Управление источниками данных (Этап 4/5)")
logger = setup_logger("kb_agent.cli.sources")


@app.command("list")
def sources_list(
    enabled_only: bool = typer.Option(
        False,
        "--enabled-only",
        "-e",
        help="Показать только включённые источники",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Показать список всех источников из канонического хранилища (SQLite)."""
    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        registry = SourceRegistry(sources_path=config.sources_path, state=state)
        sources = registry.list(enabled_only=enabled_only)
    finally:
        state.close()

    if json_output:
        _output_json(
            {
                "sources": [
                    {
                        "source_id": s.id,
                        "source_type": s.type,
                        "url": s.url,
                        "enabled": s.enabled,
                        "last_checked": s.last_checked,
                        "last_updated": s.last_updated,
                    }
                    for s in sources
                ]
            }
        )
        return

    if not sources:
        typer.echo(
            "Источников не найдено. Добавьте источник: kb-agent sources add telegram https://t.me/s/rubiconzone"
        )
        return
    typer.echo("Источники:")
    for s in sources:
        status = "✓ включён" if s.enabled else "✗ выключен"
        typer.echo(f"  {s.id} | {s.type} | {s.url} | {status}")
        if s.last_checked:
            typer.echo(f"    Последняя проверка: {s.last_checked}")
        if s.last_updated:
            typer.echo(f"    Последнее обновление: {s.last_updated}")


@app.command("add")
def sources_add(
    source_type: str = typer.Argument(..., help="Тип источника: telegram или youtube"),
    url: str = typer.Argument(
        ..., help="URL источника (https://t.me/s/канал или https://youtube.com/@kanal)"
    ),
    source_id: str = typer.Option(
        "",
        "--id",
        help="Идентификатор источника (генерируется автоматически, если не указан)",
    ),
    enabled: bool = typer.Option(
        True,
        "--enabled/--disabled",
        help="Включить источник сразу",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Добавить новый источник в каноническое хранилище (SQLite)."""
    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        registry = SourceRegistry(sources_path=config.sources_path, state=state)

        # Нормализуем и валидируем тип + URL (P2-13).
        try:
            norm_id, norm_url, norm_type = normalize_source(source_type, url, source_id)
        except ValueError as exc:
            msg = str(exc)
            if json_output:
                _output_json({"error": msg})
            else:
                typer.echo(msg, err=True)
            raise typer.Exit(code=1)
        source_id, url, source_type = norm_id, norm_url, norm_type

        # Проверяем дубликат
        if registry.get(source_id):
            msg = f"Источник {source_id} уже существует."
            if json_output:
                _output_json({"error": msg, "source_id": source_id})
            else:
                typer.echo(msg, err=True)
            raise typer.Exit(code=1)

        cfg = SourceConfig(
            id=source_id,
            type=source_type,
            url=url,
            enabled=enabled,
        )
        if registry.add(cfg):
            result = {
                "action": "add_source",
                "source_id": source_id,
                "source_type": source_type,
                "url": url,
                "enabled": enabled,
                "status": "saved",
                "file": str(config.sources_path),
            }
            if json_output:
                _output_json(result)
            else:
                typer.echo("✓ Источник добавлен:")
                typer.echo(f"  source_id: {source_id}")
                typer.echo(f"  тип: {source_type}")
                typer.echo(f"  URL: {url}")
                typer.echo(f"  включён: {enabled}")
        else:
            msg = f"Не удалось добавить источник {source_id}."
            if json_output:
                _output_json({"error": msg, "source_id": source_id})
            else:
                typer.echo(msg, err=True)
            raise typer.Exit(code=1)
    finally:
        state.close()


@app.command("remove")
def sources_remove(
    source_id: str = typer.Argument(..., help="Идентификатор источника"),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Удалить источник из канонического хранилища (SQLite)."""
    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        registry = SourceRegistry(sources_path=config.sources_path, state=state)

        if not registry.get(source_id):
            msg = f"Источник {source_id} не найден."
            if json_output:
                _output_json({"error": msg, "source_id": source_id})
            else:
                typer.echo(msg, err=True)
            raise typer.Exit(code=1)

        if registry.remove(source_id):
            result = {
                "action": "remove_source",
                "source_id": source_id,
                "status": "removed",
            }
            if json_output:
                _output_json(result)
            else:
                typer.echo(f"✓ Источник {source_id} удалён.")
        else:
            msg = f"Не удалось удалить источник {source_id}."
            if json_output:
                _output_json({"error": msg, "source_id": source_id})
            else:
                typer.echo(msg, err=True)
            raise typer.Exit(code=1)
    finally:
        state.close()


def _toggle_source(source_id: str, enabled: bool, json_output: bool) -> None:
    """Включить или выключить источник в каноническом хранилище (SQLite)."""
    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        registry = SourceRegistry(sources_path=config.sources_path, state=state)

        source = registry.get(source_id)
        if not source:
            msg = f"Источник {source_id} не найден."
            if json_output:
                _output_json({"error": msg, "source_id": source_id})
            else:
                typer.echo(msg, err=True)
            raise typer.Exit(code=1)

        if source.enabled == enabled:
            msg = f"Источник {source_id} уже {'включён' if enabled else 'выключен'}."
            if json_output:
                _output_json(
                    {
                        "source_id": source_id,
                        "enabled": enabled,
                        "status": "unchanged",
                        "message": msg,
                    }
                )
            else:
                typer.echo(msg)
            return

        registry.set_enabled(source_id, enabled)
        result = {
            "action": "enable_source",
            "source_id": source_id,
            "enabled": enabled,
            "status": "saved",
        }
        if json_output:
            _output_json(result)
        else:
            typer.echo(f"✓ Источник {source_id} {'включён' if enabled else 'выключен'}.")
    finally:
        state.close()


@app.command("enable")
def sources_enable(
    source_id: str = typer.Argument(..., help="Идентификатор источника"),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Включить источник в каноническом хранилище (SQLite)."""
    _toggle_source(source_id, True, json_output)


@app.command("disable")
def sources_disable(
    source_id: str = typer.Argument(..., help="Идентификатор источника"),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Выключить источник в каноническом хранилище (SQLite)."""
    _toggle_source(source_id, False, json_output)
