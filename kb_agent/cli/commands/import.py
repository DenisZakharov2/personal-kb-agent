"""Команда импорта существующих Markdown/TXT файлов в vault (раздел 9 аудита).

`kb-agent import <путь>` сканирует папку с .md/.txt, парсит YAML frontmatter,
индексирует в notes + notes_fts и сохраняет исходное содержимое отдельно от
суммаризации. Идемпотентен: повторный импорт не создаёт дублей
(по note_id и content_hash), при изменении источника обновляет существующую
заметку (revision + 1).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import typer

from kb_agent.cli.output import output_json as _output_json
from kb_agent.config import load_config
from kb_agent.core.import_core import _parse_frontmatter
from kb_agent.storage.state import StateManager
from kb_agent.storage.vault import VaultManager
from kb_agent.utils.logger import setup_logger
from kb_agent.utils.slug import slugify

app = typer.Typer(help="Импорт файлов базы знаний")
logger = setup_logger("kb_agent.cli.commands.import")


def _scan_files(source_path: Path) -> list[Path]:
    source_path = Path(source_path)
    if not source_path.exists():
        return []
    files: list[Path] = []
    for ext in ("*.md", "*.txt"):
        files.extend(source_path.rglob(ext))
    return sorted(files)


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def import_path(
    config, state: StateManager, vault: VaultManager, source_path: Path, dry_run: bool = False
) -> dict:
    """Импортирует .md/.txt файлы из папки. Возвращает отчёт."""
    files = _scan_files(source_path)
    report = {
        "total_files": len(files),
        "imported": 0,
        "updated": 0,
        "skipped": 0,
        "errors": 0,
        "details": [],
        "skipped_files": [],
    }

    for f in files:
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            fm, body = _parse_frontmatter(content)

            title = str(fm.get("title") or f.stem)
            topic = str(fm.get("topic", "Общее"))
            tags = fm.get("tags", [])
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            elif not isinstance(tags, list):
                tags = [str(tags)] if tags is not None else []
            tags = [str(t).strip() for t in tags if str(t).strip()]
            tags_str = ",".join(tags)

            source_type = str(fm.get("source", "import"))
            source_id = str(fm.get("source_id") or f"import_{slugify(f.stem)}")
            external_id = str(fm.get("external_id", ""))
            canonical_url = str(fm.get("url", ""))
            received_at = str(
                fm.get("received_at") or datetime.now(timezone.utc).isoformat()
            )
            note_id = str(fm.get("note_id") or f"import_{_content_hash(str(f))[:16]}")

            full_content = body.strip()
            hash_now = _content_hash(content)

            # Идемпотентность: такой же note_id с тем же содержимым — пропуск.
            existing = state.get_note(note_id)
            if existing:
                if existing.get("content_hash") == hash_now:
                    report["skipped"] += 1
                    report["skipped_files"].append(str(f))
                    report["details"].append(f"Уже импортировано без изменений: {f}")
                    continue
                # То же note_id, другое содержимое — обновление (revision + 1).
                is_update = True
            else:
                is_update = False

            if dry_run:
                action = "Обновить" if is_update else "Импортировать"
                report["details"].append(f"{action}: {f} → note_id={note_id}")
                if is_update:
                    report["updated"] += 1
                else:
                    report["imported"] += 1
                continue

            file_path = vault.write_note(
                note_id=note_id,
                source_id=source_id,
                source_type=source_type,
                external_id=external_id,
                canonical_url=canonical_url,
                published_at=fm.get("published_at"),
                received_at=received_at,
                title=title,
                tags=tags_str,
                topic=topic,
                content=full_content,
            )

            state.index_note(
                note_id=note_id,
                title=title,
                tags=tags_str,
                topic=topic,
                content=full_content,
                source_id=source_id,
                external_id=external_id,
                canonical_url=canonical_url,
                file_path=str(file_path),
                content_hash=hash_now,
                revision=1,
            )

            # Исходное содержимое отдельно от суммаризации (раздел 9 аудита).
            state.save_raw_content(
                note_id=note_id,
                raw_content=full_content,
                source_id=source_id,
                external_id=external_id,
                canonical_url=canonical_url,
                raw_hash=hash_now,
            )

            if is_update:
                report["updated"] += 1
                report["details"].append(f"Обновлено: {f} → {file_path} (note_id={note_id})")
            else:
                report["imported"] += 1
                report["details"].append(f"Импортировано: {f} → {file_path}")

        except Exception as exc:
            report["errors"] += 1
            report["details"].append(f"Ошибка при импорте {f}: {exc}")
            logger.exception("Ошибка импорта %s", f)

    return report


@app.command(name="import")
def import_command(
    path: Path = typer.Argument(..., help="Папка с .md/.txt файлами для импорта"),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Только показать, что будет импортировано, без записи в vault и БД",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Импортирует Markdown/TXT файлы из папки в базу знаний."""
    config = load_config()
    state = StateManager(config.db_path)
    vault = VaultManager(config)
    try:
        state.init_db()
        report = import_path(config, state, vault, path, dry_run=dry_run)
    finally:
        state.close()

    if json_output:
        _output_json(report)
    else:
        typer.echo(
            f"Импорт завершён: файлов {report['total_files']}, "
            f"импортировано {report['imported']}, обновлено {report['updated']}, "
            f"пропущено {report['skipped']}, ошибок {report['errors']}."
        )
        for detail in report["details"]:
            typer.echo(f"  - {detail}")
