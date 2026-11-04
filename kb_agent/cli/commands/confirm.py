"""Команда подтверждения pending-запроса (Этап 4).

kb-agent confirm <change_id> — подтвердить и применить pending-запрос write-операции.
"""

from __future__ import annotations

import typer

from kb_agent.config import load_config
from kb_agent.storage.pending import PendingManager
from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger
from kb_agent.cli.output import output_json as _output_json

logger = setup_logger("kb_agent.cli.confirm")


def confirm_command(
    change_id: str = typer.Argument(..., help="Идентификатор pending-запроса (change_id)"),
    revision: int = typer.Option(
        ...,
        "--revision",
        "-r",
        help="Ревизия pending-запроса для оптимистичной блокировки (см. kb-agent pending)",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Подтвердить и применить pending-запрос write-операции (человеческое подтверждение)."""
    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        pm = PendingManager(state)
        success = pm.confirm_change(change_id, revision=revision, confirmed_by="user")
        change = pm.get_change(change_id)
        if not change:
            result = {
                "change_id": change_id,
                "status": "not_found",
                "message": f"Pending-запрос с change_id {change_id} не найден.",
            }
            if json_output:
                _output_json(result)
            else:
                typer.echo(f"Pending-запрос с change_id {change_id} не найден.")
            raise typer.Exit(code=1)
        if success:
            result = {
                "change_id": change_id,
                "status": "applied",
                "action": change["action"],
                "entity_type": change["entity_type"],
                "entity_id": change["entity_id"],
                "message": f"Pending-запрос {change_id} подтверждён и применён.",
            }
            if json_output:
                _output_json(result)
            else:
                typer.echo(f"✓ Pending-запрос {change_id} подтверждён и применён.")
                typer.echo(f"  Действие: {change['action']}")
                typer.echo(f"  Сущность: {change['entity_type']} / {change['entity_id']}")
                typer.echo(f"  Статус: applied")
        else:
            if change["status"] != "pending":
                result = {
                    "change_id": change_id,
                    "status": change["status"],
                    "message": f"Pending-запрос {change_id} уже имеет статус {change['status']} и не может быть подтверждён повторно.",
                }
                if json_output:
                    _output_json(result)
                else:
                    typer.echo(
                        f"Pending-запрос {change_id} уже имеет статус {change['status']} "
                        f"и не может быть подтверждён повторно."
                    )
            else:
                result = {
                    "change_id": change_id,
                    "status": "failed",
                    "message": f"Ошибка при применении pending-запроса {change_id}.",
                }
                if json_output:
                    _output_json(result)
                else:
                    typer.echo(f"✗ Ошибка при применении pending-запроса {change_id}.")
            raise typer.Exit(code=1)
    finally:
        state.close()
