"""Общий вывод CLI в формате JSON (Этап 4)."""

from __future__ import annotations

import json

import typer


def output_json(data: dict) -> None:
    """Печатает данные в формате JSON (UTF-8, отступ 2)."""
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str))
