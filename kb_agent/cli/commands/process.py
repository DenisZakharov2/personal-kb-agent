"""Команды конвейера наполнения базы знаний (Этап 2) и аудита/реиндексации (Волна 1).

kb-agent process run   — запустить сбор и суммаризацию
kb-agent process status — показать статус базы данных
kb-agent process digest — запустить конвейер и вывести дайджест
kb-agent reindex        — перестроить notes_fts из файлов vault
kb-agent doctor         — проверка целостности базы знаний
kb-agent retry <id>     — повторная обработка элемента очереди
kb-agent backup         — экспорт SQLite + vault в архив
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
import yaml

from kb_agent.config import load_config
from kb_agent.processing.pipeline import Pipeline, run_digest
from kb_agent.storage.state import StateManager
from kb_agent.storage.vault import VaultManager
from kb_agent.utils.logger import setup_logger
from kb_agent.cli.output import output_json as _output_json

app = typer.Typer(help="Конвейер наполнения базы знаний")

logger = setup_logger("kb_agent.process")


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

            # P2-14: ошибки сбора/обработки выводятся и влияют на exit code.
            coll_errors = result.get("collection", {}).get("errors", [])
            processing_failed = result.get("processing", {}).get("failed", 0)
            if not result.get("ok", True) or coll_errors or processing_failed:
                if coll_errors:
                    typer.echo("", err=True)
                    typer.echo("Ошибки сбора источников:", err=True)
                    for err in coll_errors:
                        typer.echo(f"  - {err}", err=True)
                if processing_failed:
                    typer.echo(
                        f"Ошибок обработки очереди: {processing_failed}", err=True
                    )
                raise typer.Exit(code=1)
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


@app.command("reindex")
def process_reindex(
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Перестроить notes_fts из существующих файлов vault (чтение frontmatter)."""
    config = load_config()
    state = StateManager(config.db_path)
    vault = VaultManager(config)
    try:
        state.init_db()
        indexed = 0
        seen_note_ids: list[str] = []
        for path in sorted(vault.list_notes()):
            parsed = vault.parse_note(path)
            note_id = parsed["note_id"] or hashlib.sha256(
                str(path).encode("utf-8")
            ).hexdigest()[:16]
            state.index_note(
                note_id=note_id,
                title=parsed["title"],
                tags=parsed["tags"],
                topic=parsed["topic"],
                content=parsed["content"],
                source_id=parsed["source_id"],
                external_id=parsed["external_id"],
                canonical_url=parsed["url"],
                file_path=str(path),
                content_hash=parsed["content_hash"],
                revision=1,
            )
            seen_note_ids.append(note_id)
            indexed += 1

        # Удаляем FTS-записи, для которых больше нет файла в vault (прюекция rebuildable).
        conn = state.connect()
        if seen_note_ids:
            placeholders = ",".join("?" * len(seen_note_ids))
            conn.execute(
                f"DELETE FROM notes_fts WHERE note_id NOT IN ({placeholders})",
                tuple(seen_note_ids),
            )
        else:
            conn.execute("DELETE FROM notes_fts")
        conn.commit()
        state.close()

        result = {
            "indexed": indexed,
            "vault_path": str(vault.vault_path),
        }
        if json_output:
            _output_json(result)
        else:
            typer.echo(f"Reindex завершён: проиндексировано {indexed} заметок.")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка reindex")
        state.close()
        if json_output:
            _output_json({"error": str(exc), "indexed": 0})
        else:
            typer.echo(f"✗ Ошибка reindex: {exc}")
        raise typer.Exit(code=1)


@app.command("doctor")
def process_doctor(
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Проверка целостности: орфаны, битые FTS-ссылки, зависшие processing, источники."""
    config = load_config()
    state = StateManager(config.db_path)
    vault = VaultManager(config)
    try:
        state.init_db()
        conn = state.connect()
        now = datetime.now(timezone.utc).isoformat()
        issues: list[dict] = []

        # 1. Орфан-файлы в vault без записи в notes_fts.
        file_note_ids: dict[str, str] = {}
        for path in sorted(vault.list_notes()):
            parsed = vault.parse_note(path)
            note_id = (parsed.get("note_id") or "").strip()
            if note_id:
                file_note_ids[note_id] = str(path)
            else:
                issues.append(
                    {
                        "kind": "orphan_file",
                        "path": str(path),
                        "detail": "нет note_id во frontmatter",
                    }
                )

        fts_ids = {
            row["note_id"]
            for row in conn.execute("SELECT note_id FROM notes_fts").fetchall()
        }
        for note_id, path in file_note_ids.items():
            if note_id not in fts_ids:
                issues.append(
                    {
                        "kind": "orphan_file",
                        "path": path,
                        "note_id": note_id,
                        "detail": "нет записи в notes_fts",
                    }
                )

        # 2. FTS-записи без файла в vault.
        for note_id in sorted(fts_ids - set(file_note_ids)):
            issues.append(
                {
                    "kind": "fts_without_file",
                    "note_id": note_id,
                    "detail": "файл в vault не найден",
                }
            )

        # 3. Зависшие processing (истёкший lease).
        stuck = conn.execute(
            """
            SELECT id, source_id, external_id, title, lease_expires_at, processing_started_at
            FROM raw_queue
            WHERE state = 'processing'
              AND (lease_expires_at IS NULL OR lease_expires_at < ?)
            """,
            (now,),
        ).fetchall()
        for row in stuck:
            issues.append(
                {
                    "kind": "stuck_processing",
                    "queue_id": row["id"],
                    "source_id": row["source_id"],
                    "external_id": row["external_id"],
                    "title": row["title"],
                    "detail": f"lease_expires_at={row['lease_expires_at']}",
                }
            )

        # 4. failed без next_retry_at (не запланирован на повтор).
        failed_no_retry = conn.execute(
            """
            SELECT id, source_id, external_id, title, last_error
            FROM raw_queue
            WHERE state = 'failed' AND next_retry_at IS NULL
            """,
        ).fetchall()
        for row in failed_no_retry:
            issues.append(
                {
                    "kind": "failed_without_retry",
                    "queue_id": row["id"],
                    "source_id": row["source_id"],
                    "external_id": row["external_id"],
                    "title": row["title"],
                    "detail": row["last_error"] or "",
                }
            )

        # 5. Расхождение sources (SQLite vs sources.yaml).
        yaml_source_ids: set[str] = set()
        sources_path = config.sources_path
        if sources_path.exists():
            try:
                data = yaml.safe_load(sources_path.read_text(encoding="utf-8")) or {}
                for item in data.get("sources", []) or []:
                    if isinstance(item, dict) and item.get("id"):
                        yaml_source_ids.add(item["id"])
            except yaml.YAMLError as exc:  # noqa: BLE001
                logger.warning("Не удалось прочитать sources.yaml: %s", exc)
        sqlite_source_ids = {
            row["source_id"] for row in conn.execute("SELECT source_id FROM sources").fetchall()
        }
        for source_id in sorted(sqlite_source_ids - yaml_source_ids):
            issues.append(
                {
                    "kind": "source_mismatch",
                    "source_id": source_id,
                    "detail": "есть в SQLite, нет в sources.yaml",
                }
            )
        for source_id in sorted(yaml_source_ids - sqlite_source_ids):
            issues.append(
                {
                    "kind": "source_mismatch",
                    "source_id": source_id,
                    "detail": "есть в sources.yaml, нет в SQLite",
                }
            )

        state.close()

        result = {
            "issues": issues,
            "count": len(issues),
            "kinds": {
                kind: sum(1 for i in issues if i["kind"] == kind)
                for kind in {
                    "orphan_file",
                    "fts_without_file",
                    "stuck_processing",
                    "failed_without_retry",
                    "source_mismatch",
                }
            },
        }
        if json_output:
            _output_json(result)
        else:
            typer.echo(f"doctor: найдено проблем — {len(issues)}")
            for kind, number in result["kinds"].items():
                if number:
                    typer.echo(f"  {kind}: {number}")
            if not issues:
                typer.echo("Целостность в порядке, проблем не выявлено.")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка doctor")
        state.close()
        if json_output:
            _output_json({"error": str(exc), "issues": [], "count": 0})
        else:
            typer.echo(f"✗ Ошибка doctor: {exc}")
        raise typer.Exit(code=1)


@app.command("retry")
def process_retry(
    queue_id: int = typer.Argument(..., help="ID элемента очереди (raw_queue.id)"),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Повторная обработка конкретного элемента очереди (сброс в state='new')."""
    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        conn = state.connect()
        cur = conn.execute(
            """
            UPDATE raw_queue
            SET state = 'new',
                next_retry_at = NULL,
                lease_expires_at = NULL,
                processing_started_at = NULL
            WHERE id = ?
            """,
            (queue_id,),
        )
        conn.commit()
        retried = cur.rowcount > 0
        state.close()

        result = {
            "queue_id": queue_id,
            "retried": retried,
            "state": "new" if retried else None,
        }
        if json_output:
            _output_json(result)
        else:
            if retried:
                typer.echo(f"✓ Элемент очереди {queue_id} отправлен на повторную обработку.")
            else:
                typer.echo(f"Элемент очереди {queue_id} не найден.")
                raise typer.Exit(code=1)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка retry")
        state.close()
        if json_output:
            _output_json({"error": str(exc), "retried": False, "queue_id": queue_id})
        else:
            typer.echo(f"✗ Ошибка retry: {exc}")
        raise typer.Exit(code=1)


@app.command("backup")
def process_backup(
    output: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        help="Путь к архиву (по умолчанию data_dir/kb_backup_<UTC>.zip)",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Экспорт SQLite + vault в zip-архив."""
    config = load_config()
    state = StateManager(config.db_path)
    vault = VaultManager(config)
    try:
        state.init_db()
        state.close()  # БД не должна быть открыта при архивации

        zip_path = Path(output) if output else (
            config.data_dir / f"kb_backup_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.zip"
        )
        zip_path.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            if config.db_path.exists():
                archive.write(config.db_path, arcname="state.db")
            for path in sorted(vault.list_notes()):
                arcname = str(Path("vault") / path.relative_to(vault.vault_path))
                archive.write(path, arcname=arcname)

        result = {"archive": str(zip_path)}
        if json_output:
            _output_json(result)
        else:
            typer.echo(f"✓ Резервная копия создана: {zip_path}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка backup")
        try:
            state.close()
        except Exception:  # noqa: BLE001
            pass
        if json_output:
            _output_json({"error": str(exc), "archive": None})
        else:
            typer.echo(f"✗ Ошибка backup: {exc}")
        raise typer.Exit(code=1)
