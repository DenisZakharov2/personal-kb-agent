"""Команда слияния дублей заметок (раздел 9 аудита).

kb-agent merge <id1> <id2> [--keep 1|2] [--dry-run] [--json]

Основная заметка остаётся, дубль архивируется (is_archived = 1),
все ссылки переназначаются на основную заметку.
"""

from __future__ import annotations

import typer

from kb_agent.config import load_config
from kb_agent.core.update import KnowledgeUpdater
from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger
from kb_agent.cli.output import output_json as _output_json

logger = setup_logger("kb_agent.cli.merge")


def merge_command(
    note_id_1: str = typer.Argument(..., help="ID основной заметки (остаётся)"),
    note_id_2: str = typer.Argument(..., help="ID дубля (архивируется/удаляется)"),
    keep: str = typer.Option(
        "1",
        "--keep",
        "-k",
        help="Какую заметку оставить: 1 или 2",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Только показать, что будет сделано",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="JSON-вывод",
    ),
) -> None:
    """Объединить две заметки: дубль архивируется, ссылки переносятся на основную."""
    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()

        # ---- Валидация ----
        if note_id_1 == note_id_2:
            error = {
                "error": "validation",
                "message": "note_id_1 и note_id_2 не могут совпадать.",
            }
            if json_output:
                _output_json(error)
            else:
                typer.echo("✗ Ошибка валидации: note_id_1 и note_id_2 не могут совпадать.")
            raise typer.Exit(code=1)

        if keep not in ("1", "2"):
            error = {
                "error": "validation",
                "message": f"Параметр --keep должен быть 1 или 2, получено: {keep!r}",
            }
            if json_output:
                _output_json(error)
            else:
                typer.echo(
                    f"✗ Ошибка валидации: --keep должен быть 1 или 2, получено: {keep!r}"
                )
            raise typer.Exit(code=1)

        keep_note_id = note_id_1 if keep == "1" else note_id_2
        drop_note_id = note_id_2 if keep == "1" else note_id_1

        keep_note = state.get_note(keep_note_id)
        drop_note = state.get_note(drop_note_id)
        if keep_note is None:
            error = {
                "error": "not_found",
                "message": f"Основная заметка не найдена: {keep_note_id}",
            }
            if json_output:
                _output_json(error)
            else:
                typer.echo(f"✗ Основная заметка не найдена: {keep_note_id}")
            raise typer.Exit(code=1)
        if drop_note is None:
            error = {
                "error": "not_found",
                "message": f"Дубль не найден: {drop_note_id}",
            }
            if json_output:
                _output_json(error)
            else:
                typer.echo(f"✗ Дубль не найден: {drop_note_id}")
            raise typer.Exit(code=1)

        conn = state.connect()
        links_count = conn.execute(
            """
            SELECT COUNT(*) AS c FROM note_links
            WHERE source_note_id = ? OR target_note_id = ?
            """,
            (drop_note_id, drop_note_id),
        ).fetchone()["c"]

        keep_raw = state.get_raw_content(keep_note_id)
        drop_raw = state.get_raw_content(drop_note_id)
        raw_content_moved = drop_raw is not None and keep_raw is None

        drop_fts = conn.execute(
            "SELECT 1 FROM notes_fts WHERE note_id = ?", (drop_note_id,)
        ).fetchone()
        fts_entries_removed = 1 if drop_fts else 0

        if dry_run:
            report = {
                "kept_note_id": keep_note_id,
                "kept_title": keep_note.get("title") or "Без названия",
                "dropped_note_id": drop_note_id,
                "dropped_title": drop_note.get("title") or "Без названия",
                "links_moved": int(links_count),
                "raw_content_moved": raw_content_moved,
                "fts_entries_removed": fts_entries_removed,
                "dry_run": True,
                "message": "План слияния (изменений не внесено)",
            }
            if json_output:
                _output_json(report)
            else:
                typer.echo("## План слияния дублей (dry-run, без изменений)")
                typer.echo("")
                typer.echo(f"  Оставить:     {keep_note_id} ('{report['kept_title']}')")
                typer.echo(f"  Архивировать: {drop_note_id} ('{report['dropped_title']}')")
                typer.echo(f"  Перенесёт связей: {report['links_moved']}")
                typer.echo(f"  Удалит из FTS: {report['fts_entries_removed']}")
                if report["raw_content_moved"]:
                    typer.echo("  Перенесёт raw_content: да")
            return

        # ---- Слияние ----
        updater = KnowledgeUpdater(config, state)
        result = updater.merge_notes(keep_note_id, drop_note_id)

        if json_output:
            output = {
                "kept_note_id": result["kept_note_id"],
                "kept_title": keep_note.get("title") or "Без названия",
                "dropped_note_id": result["dropped_note_id"],
                "dropped_title": drop_note.get("title") or "Без названия",
                "links_moved": result["links_moved"],
                "raw_content_moved": result["raw_content_moved"],
                "fts_entries_removed": result["fts_entries_removed"],
                "dry_run": False,
            }
            _output_json(output)
        else:
            typer.echo("✓ Дубли объединены.")
            typer.echo(f"  Оставлено:     {keep_note_id} ('{keep_note.get('title') or 'Без названия'}')")
            typer.echo(f"  Архивировано:  {drop_note_id} ('{drop_note.get('title') or 'Без названия'}')")
            typer.echo(f"  Перенесено связей: {result['links_moved']}")
            typer.echo(f"  Удалено из FTS: {result['fts_entries_removed']}")
            if result["raw_content_moved"]:
                typer.echo("  Перенесён raw_content: да")
    finally:
        state.close()
