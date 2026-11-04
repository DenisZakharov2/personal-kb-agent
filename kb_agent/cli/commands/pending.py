"""Команда просмотра pending-запросов (Этап 4).

kb-agent pending — показать список pending-запросов (ожидающих подтверждения).
"""

from __future__ import annotations

import json
import typer

from kb_agent.config import load_config
from kb_agent.storage.pending import PendingManager
from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger

app = typer.Typer(help="Просмотр pending-запросов write-операций (Этап 4)")
logger = setup_logger("kb_agent.cli.pending")


def _output_json(data: dict) -> None:
    """Выводит данные в формате JSON."""
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2))


@app.command(name="pending")
def pending_command(
    status: str = typer.Option(
        "pending",
        "--status",
        "-s",
        help="Фильтр по статусу: pending, confirmed, applied, rejected, failed",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Показать список pending-запросов write-операций."""
    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        pm = PendingManager(state)
        changes = pm.list_changes(status=status)

        if json_output:
            _output_json(
                {
                    "status": status,
                    "count": len(changes),
                    "changes": [
                        {
                            "change_id": c["change_id"],
                            "action": c["action"],
                            "entity_type": c["entity_type"],
                            "entity_id": c["entity_id"],
                            "parameters": json.loads(c["parameters"] or "{}"),
                            "status": c["status"],
                            "created_at": c["created_at"],
                            "confirmed_at": c["confirmed_at"],
                            "applied_at": c["applied_at"],
                            "confirmed_by": c["confirmed_by"],
                            "result": json.loads(c["result"] or "{}") if c["result"] else None,
                        }
                        for c in changes
                    ],
                }
            )
            return

        if not changes:
            typer.echo(f"Pending-запросов со статусом '{status}' не найдено.")
            return
        typer.echo(f"Pending-запросы (статус: {status}):")
        for c in changes:
            params = c["parameters"] or "{}"
            typer.echo(
                f"  {c['change_id']} | {c['action']} | {c['entity_type']}/{c['entity_id']} | {c['status']}"
            )
            typer.echo(f"    Параметры: {params[:120]}")
            typer.echo(f"    Создан: {c['created_at']}")
    finally:
        state.close()
