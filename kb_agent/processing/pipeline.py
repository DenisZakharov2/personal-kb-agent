"""Конвейер обработки базы знаний (Этап 2/5).

Сбор из источников → запись в raw_queue → Relevance Gate → суммаризация через LLM
→ запись в vault (тематические папки) → обновление FTS5-индекса → Каталог.md → Дайджест.md.
Один поток, без дублей конвейеров.
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

from kb_agent.config import AppConfig
from kb_agent.core.prompts import (
    SUMMARIZE_PROMPT_NAME,
    ensure_prompt_seeded,
    get_active_system_prompt,
)
from kb_agent.core.relevance import RelevanceGate
from kb_agent.processing.llm_client import LLMClient
from kb_agent.sources.manager import run_collection
from kb_agent.sources.registry import SourceRegistry, ensure_sources_file
from kb_agent.storage.catalog import CatalogGenerator
from kb_agent.storage.state import StateManager
from kb_agent.storage.vault import VaultManager, atomic_write_text
from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.processing.pipeline")


class Pipeline:
    """Один конвейер наполнения базы знаний."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.state = StateManager(config.db_path)
        self.llm = LLMClient(config)
        self.vault = VaultManager(config)
        self.catalog = CatalogGenerator(self.vault)
        self.relevance: RelevanceGate | None = None

    def _load_profile(self) -> dict:
        """Загружает user_profile.yaml или возвращает пустой профиль."""
        profile_path = Path(self.config.profile_path)
        if not profile_path.exists():
            return {}
        try:
            data = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
            return data.get("user", {}) if isinstance(data, dict) else {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось загрузить профиль: %s", exc)
            return {}

    def run(self) -> dict:
        """Запускает полный конвейер: сбор + обработка.

        Возвращает дайджест запуска.
        """
        logger.info("=== Запуск конвейера наполнения ===")
        self.state.init_db()
        # Создаём файл источников, если его нет (R-05).
        sources_path = ensure_sources_file(config=self.config)
        sources_text = sources_path.read_text(encoding="utf-8") if sources_path.exists() else ""
        if not any(line.strip().startswith("- ") for line in sources_text.splitlines()):
            logger.warning(
                "Реестр источников пуст (%s). Добавьте источники: kb-agent sources add ...",
                sources_path,
            )
        # Однократный импорт источников из YAML в SQLite при пустой таблице
        # (на чистой установке или в CI). Это делается только здесь в Pipeline.run(),
        # а не в SourceRegistry.load(), чтобы не переимпортировать после удаления источников.
        registry = SourceRegistry(sources_path=sources_path, state=self.state)
        try:
            registry.load()
            if not registry.list() and sources_path.exists():
                imported = registry.import_from_yaml(sources_path)
                if imported:
                    logger.info("Выполнен начальный импорт %s источников из %s", imported, sources_path)
        except sqlite3.OperationalError as exc:
            # Таблица sources может не существовать, если init_db был замокнут (тесты)
            logger.debug("Пропуск импорта источников: %s", exc)
        # Импортируем базовую версию системного промпта в prompt_history,
        # чтобы работали откат промпта и self-improve.
        ensure_prompt_seeded(self.config, self.state)

        # Загружаем профиль и настраиваем Relevance Gate
        profile = self._load_profile()
        self.relevance = RelevanceGate(profile)
        logger.info("Профиль загружен: темы=%s", list(self.relevance.topics))

        # Шаг 1: Сбор из всех активных источников в raw_queue
        collection = run_collection(config=self.config, state=self.state)
        tg_stats = {"new": 0, "spam": 0, "existing": 0}
        yt_stats = {"new": 0, "skipped": 0, "with_subtitles": 0}
        for sid, stats in collection.by_source.items():
            if sid.startswith("tg_"):
                tg_stats["new"] += stats.get("new", 0)
                tg_stats["spam"] += stats.get("spam", 0)
                tg_stats["existing"] += stats.get("skipped", stats.get("existing", 0))
            elif sid.startswith("yt_"):
                yt_stats["new"] += stats.get("new", 0)
                yt_stats["skipped"] += stats.get("skipped", 0)
                yt_stats["with_subtitles"] += stats.get("with_subtitles", 0)

        # Шаг 2: Обработка очереди (с Relevance Gate)
        digest = self._process_queue()

        # Шаг 3: Генерация Каталог.md
        catalog_path = self.catalog.generate()
        logger.info("Каталог обновлён: %s", catalog_path)

        # Шаг 4: Запись Дайджест.md в vault
        digest_path = self._write_vault_digest(digest, tg_stats, yt_stats)
        logger.info("Дайджест записан: %s", digest_path)

        collection_errors = list(collection.errors or [])
        processing_failed = digest.get("failed", 0)
        ok = not collection_errors and processing_failed == 0

        # P2-14: Health summary — статус каждого источника (ok/failed/skipped).
        error_by_source: dict[str, str] = {}
        for err in collection_errors:
            if ": " in err:
                source_id, text = err.split(": ", 1)
                error_by_source[source_id] = text
            else:
                error_by_source[err] = err

        health: dict[str, dict] = {}
        for source_id, stats in collection.by_source.items():
            if source_id in collection.skipped:
                # Выключенный источник не собирался — статус skipped.
                continue
            error = error_by_source.get(source_id)
            if error:
                health[source_id] = {
                    "status": "failed",
                    "new": 0,
                    "skipped": 0,
                    "failed": 1,
                    "error": error,
                }
            else:
                health[source_id] = {
                    "status": "ok",
                    "new": stats.get("new", 0),
                    "skipped": stats.get("skipped", 0),
                    "failed": 0,
                    "error": None,
                }
        for source_id, reason in collection.skipped.items():
            health[source_id] = {
                "status": "skipped",
                "new": 0,
                "skipped": 1,
                "failed": 0,
                "error": reason,
            }

        summary = {
            "total_sources": len(health),
            "healthy": sum(1 for h in health.values() if h["status"] == "ok"),
            "failed": sum(1 for h in health.values() if h["status"] == "failed"),
            "skipped": sum(1 for h in health.values() if h["status"] == "skipped"),
        }

        # Итоговый дайджест (P2-14: ошибки сбора/обработки попадают в итог,
        # флаг ok определяет политику exit code в CLI).
        result = {
            "collection": {
                "telegram": tg_stats,
                "youtube": yt_stats,
                "errors": collection_errors,
            },
            "health": health,
            "summary": summary,
            "processing": digest,
            "catalog": str(catalog_path),
            "vault_digest": str(digest_path),
            "ok": ok,
        }
        logger.info("=== Конвейер завершён (ok=%s, sources=%s) ===", ok, summary)
        return result

    def _process_queue(self) -> dict:
        """Обрабатывает элементы из raw_queue с lease-захватом и повторами.

        Использует claim_next для атоматического захвата заданий.
        Повторяет failed элементы после истечения next_retry_at.
        Освобождает зависшие processing элементы после истечения lease.
        """
        created = 0
        updated = 0
        skipped = 0
        failed = 0
        processed_total = 0
        relevance_skipped = 0
        lease_seconds = 300  # 5 минут на обработку одного элемента

        while True:
            item = self.state.claim_next(lease_seconds=lease_seconds)
            if item is None:
                break

            queue_id = item["id"]
            processed_total += 1

            try:
                # Relevance Gate — проверка перед суммаризацией
                relevance = self.relevance.check(
                    raw_text=item["raw_text"] or "",
                    title=item["title"] or "",
                )
                if not relevance.relevant:
                    self.state.mark_skipped(queue_id, relevance.reason)
                    skipped += 1
                    relevance_skipped += 1
                    logger.info(
                        "Пропуск (relevance) элемент %s: %s",
                        queue_id, relevance.reason,
                    )
                    continue

                # Определяем тип источника
                source_type = item["source_id"].split("_")[0]  # tg или yt

                # Идемпотентно: при повторном сборе того же источника
                # обновляем существующую заметку (revision + 1), а не создаём новую.
                existing = self.state.get_note_by_external(item["source_id"], item["external_id"])
                if existing:
                    note_id = existing["note_id"]
                    revision = int(existing["revision"] or 1) + 1
                    is_update = True
                else:
                    # Проверяем, есть ли уже сохранённый note_id в очереди
                    # (идемпотентность при повторе после частичного сбоя).
                    queue_item = self.state.get_queue_item(queue_id)
                    stored_note_id = queue_item.get("note_id") if queue_item else None
                    if stored_note_id:
                        note_id = stored_note_id
                        revision = 1
                        is_update = False
                    else:
                        note_id = self._make_note_id(item["received_at"])
                        revision = 1
                        is_update = False

                # Структурированная суммаризация через LLM (P2-10: JSON-схема,
                # валидация темы против профиля, повтор при невалидной схеме)
                # Активная версия промпта берётся из prompt_history (её можно
                # редактировать и откатывать через self-improve).
                summarize_prompt = get_active_system_prompt(
                    self.state, SUMMARIZE_PROMPT_NAME
                )
                summary = self.llm.summarize_structured(
                    raw_text=item["raw_text"] or "",
                    source_type=source_type,
                    source_name=item["source_id"],
                    topics=list(self.relevance.topics) if self.relevance else None,
                    system_prompt=summarize_prompt,
                )

                if not summary.content.strip():
                    self.state.mark_failed(queue_id, "пустой ответ от LLM")
                    failed += 1
                    continue

                tags = summary.tags
                topic = summary.topic
                content = summary.content
                final_title = summary.title or item["title"] or "Без названия"

                # Запись в vault: существующий файл с таким note_id
                # перезаписывается на месте (без дублей файлов).
                file_path = self.vault.write_note(
                    note_id=note_id,
                    source_id=item["source_id"],
                    source_type=source_type,
                    external_id=item["external_id"],
                    canonical_url=item["canonical_url"] or "",
                    published_at=item["published_at"] if item["published_at"] else None,
                    received_at=item["received_at"],
                    title=final_title,
                    tags=",".join(tags),
                    topic=topic,
                    content=content,
                )

                # Сохраняем note_id в очереди для идемпотентного повтора
                # после частичного сбоя (файл записан, но индекс ещё нет).
                self.state.set_queue_item_note_id(queue_id, note_id)

                # Метаданные генерации (модель, версия промпта, токены, задержка, стоимость).
                llm_response = self.llm.last_response
                active_prompt = self.state.get_active_prompt(
                    SUMMARIZE_PROMPT_NAME, prompt_type="summarize"
                )
                self.state.save_generation_log(
                    note_id=note_id,
                    operation="summarize",
                    model=self.llm.model,
                    prompt_name=SUMMARIZE_PROMPT_NAME,
                    prompt_version=active_prompt["version"] if active_prompt else None,
                    prompt_tokens=llm_response.prompt_tokens if llm_response else 0,
                    completion_tokens=llm_response.completion_tokens if llm_response else 0,
                    latency_ms=llm_response.latency_ms if llm_response else 0.0,
                    cost_usd=0.0,
                )

                # Индексируем в FTS5 (при дубле note_id обновляется запись, revision + 1).
                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
                self.state.remove_from_index(note_id)
                self.state.index_note(
                    note_id=note_id,
                    title=final_title,
                    tags=",".join(tags),
                    topic=topic,
                    content=content,
                    source_id=item["source_id"],
                    external_id=item["external_id"],
                    canonical_url=item["canonical_url"] or "",
                    file_path=str(file_path),
                    content_hash=content_hash,
                    revision=revision,
                )

                # Исходное содержимое хранится отдельно от суммаризации,
                # чтобы заметку можно было пересобрать другой моделью/промптом.
                self.state.save_raw_content(
                    note_id=note_id,
                    raw_content=item["raw_text"] or "",
                    source_id=item["source_id"],
                    external_id=item["external_id"],
                    canonical_url=item["canonical_url"] or "",
                    raw_hash=item["raw_hash"],
                )

                # Связываем с уже накопленными заметками по общим тегам
                self._link_related_notes(note_id, tags)

                self.state.mark_processed(queue_id)
                # Помечаем external_id как обработанный для дедупликации YouTube
                self.state.mark_processed_key(item["external_id"])
                if is_update:
                    updated += 1
                else:
                    created += 1

            except Exception as exc:
                logger.exception("Ошибка при обработке элемента %s", queue_id)
                self.state.mark_failed(queue_id, str(exc))
                failed += 1

        return {
            "processed": processed_total,
            "created": created,
            "updated": updated,
            "skipped": skipped,
            "relevance_skipped": relevance_skipped,
            "failed": failed,
        }

    def _write_vault_digest(
        self,
        processing: dict,
        tg_stats: dict,
        yt_stats: dict,
    ) -> Path:
        """Записывает Дайджест.md в vault после запуска конвейера."""
        digest_path = self.vault.vault_path / "Дайджест.md"

        lines: list[str] = []
        lines.append("# Дайджест запуска конвейера")
        lines.append("")
        lines.append(
            f"Дата: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
        )
        lines.append("")

        lines.append("## Сбор из источников")
        lines.append(
            f"- Telegram: новых {tg_stats['new']}, спама {tg_stats['spam']}, "
            f"уже было {tg_stats['existing']}"
        )
        lines.append(
            f"- YouTube: новых {yt_stats['new']}, пропущено {yt_stats['skipped']}, "
            f"с субтитрами {yt_stats['with_subtitles']}"
        )
        lines.append("")

        lines.append("## Обработка очереди")
        lines.append(f"- Всего обработано: {processing['processed']}")
        lines.append(f"- Создано заметок: {processing['created']}")
        lines.append(f"- Обновлено заметок: {processing.get('updated', 0)}")
        relevance_skipped = processing.get('relevance_skipped', 0)
        other_skipped = processing['skipped'] - relevance_skipped
        lines.append(f"- Пропущено (relevance): {relevance_skipped}")
        lines.append(f"- Пропущено (другое): {max(0, other_skipped)}")
        lines.append(f"- Ошибок: {processing['failed']}")
        lines.append("")

        atomic_write_text(digest_path, "\n".join(lines), backup=True)
        return digest_path

    @staticmethod
    def _make_note_id(received_at: str) -> str:
        """note_id без пробелов: 2026-11-04T10_ab12cd34."""
        stamp = (received_at or "").strip()
        try:
            stamp = datetime.fromisoformat(stamp).strftime("%Y-%m-%dT%H")
        except ValueError:
            if stamp:
                stamp = stamp[:13].replace(" ", "T").replace(":", "")
            else:
                stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H")
        return f"{stamp}_{uuid.uuid4().hex[:8]}"

    def _link_related_notes(self, note_id: str, tags: list[str]) -> None:
        """Создаёт связи `references` с заметками по общим тегам (топ-3)."""
        my_tags = {
            tag.strip().lower()
            for tag in tags
            if isinstance(tag, str) and tag.strip()
        }
        if not my_tags:
            return
        conn = self.state.connect()
        rows = conn.execute(
            "SELECT note_id, tags FROM notes_fts WHERE note_id != ?", (note_id,)
        ).fetchall()
        scored: list[tuple[int, str]] = []
        for row in rows:
            other_tags = {
                tag.strip().lower() for tag in (row["tags"] or "").split(",") if tag.strip()
            }
            common = my_tags & other_tags
            if common:
                scored.append((len(common), row["note_id"]))
        for _, other_note_id in sorted(scored, reverse=True)[:3]:
            self.state.add_link(note_id, other_note_id, "references")

    def close(self) -> None:
        self.state.close()


def run_digest(config: AppConfig) -> str:
    """Формирует текстовой дайджест запуска для пользователя."""
    pipeline = Pipeline(config)
    try:
        result = pipeline.run()
    finally:
        pipeline.close()

    lines: list[str] = []
    lines.append("# Дайджест запуска конвейера")
    lines.append("")
    lines.append(f"Дата: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    lines.append("")

    coll = result["collection"]
    tg = coll["telegram"]
    yt = coll["youtube"]
    lines.append("## Сбор из источников")
    lines.append(f"- Telegram: новых {tg['new']}, спама {tg['spam']}, уже было {tg['existing']}")
    lines.append(f"- YouTube: новых {yt['new']}, пропущено {yt['skipped']}, с субтитрами {yt['with_subtitles']}")
    lines.append("")

    proc = result["processing"]
    lines.append("## Обработка очереди")
    lines.append(f"- Всего обработано: {proc['processed']}")
    lines.append(f"- Создано заметок: {proc['created']}")
    lines.append(f"- Пропущено (relevance): {proc.get('relevance_skipped', 0)}")
    lines.append(f"- Пропущено (другое): {proc['skipped']}")
    lines.append(f"- Ошибок: {proc['failed']}")
    lines.append("")

    return "\n".join(lines)
