"""Конвейер обработки базы знаний (Этап 2/5).

Сбор из источников → запись в raw_queue → Relevance Gate → суммаризация через LLM
→ запись в vault (тематические папки) → обновление FTS5-индекса → Каталог.md → Дайджест.md.
Один поток, без дублей конвейеров.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

from kb_agent.config import AppConfig
from kb_agent.core.relevance import RelevanceGate
from kb_agent.processing.llm_client import LLMClient
from kb_agent.processing.kb_writer import KBWriter
from kb_agent.sources.manager import SourceManager, run_collection
from kb_agent.storage.catalog import CatalogGenerator
from kb_agent.storage.state import StateManager
from kb_agent.storage.vault import VaultManager
from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.processing.pipeline")


class Pipeline:
    """Один конвейер наполнения базы знаний."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.state = StateManager(config.db_path)
        self.llm = LLMClient(config)
        self.writer = KBWriter(config)
        self.vault = VaultManager(config)
        self.catalog = CatalogGenerator(self.vault)
        self.relevance: Optional[RelevanceGate] = None

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

        # Итоговый дайджест
        result = {
            "collection": {
                "telegram": tg_stats,
                "youtube": yt_stats,
            },
            "processing": digest,
            "catalog": str(catalog_path),
            "vault_digest": str(digest_path),
        }
        logger.info("=== Конвейер завершён ===")
        return result

    def _process_queue(self) -> dict:
        """Обрабатывает все элементы в состоянии 'new' из raw_queue."""
        created = 0
        skipped = 0
        failed = 0
        processed_total = 0
        relevance_skipped = 0

        while True:
            item = self.state.get_next_new()
            if item is None:
                break

            queue_id = item["id"]
            self.state.mark_processing(queue_id)
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

                # Генерируем note_id
                note_id = f"{item['received_at'][:13]}_{uuid.uuid4().hex[:8]}"

                # Определяем тип источника
                source_type = item["source_id"].split("_")[0]  # tg или yt

                # Суммаризация через LLM
                llm_response = self.llm.summarize(
                    raw_text=item["raw_text"] or "",
                    source_type=source_type,
                    source_name=item["source_id"],
                )

                if llm_response.error or not llm_response.text.strip():
                    self.state.mark_failed(
                        queue_id,
                        llm_response.error or "пустой ответ от LLM",
                    )
                    failed += 1
                    continue

                # Извлекаем теги из ответа LLM (простой парсинг)
                tags = self._extract_tags(llm_response.text)
                topic = self._guess_topic(llm_response.text)

                # Пишем заметку в vault (тематические папки)
                self.vault.write_note(
                    note_id=note_id,
                    source_id=item["source_id"],
                    source_type=source_type,
                    external_id=item["external_id"],
                    canonical_url=item["canonical_url"] or "",
                    published_at=item["published_at"] if item["published_at"] else None,
                    received_at=item["received_at"],
                    title=item["title"] or "Без названия",
                    tags=",".join(tags),
                    topic=topic,
                    content=llm_response.text,
                )

                # Индексируем в FTS5
                self.state.index_note(
                    note_id=note_id,
                    title=item["title"] or "Без названия",
                    tags=",".join(tags),
                    topic=topic,
                    content=llm_response.text,
                )

                self.state.mark_processed(queue_id)
                created += 1

            except Exception as exc:  # noqa: BLE001
                logger.exception("Ошибка при обработке элемента %s", queue_id)
                self.state.mark_failed(queue_id, str(exc))
                failed += 1

        return {
            "processed": processed_total,
            "created": created,
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
        lines.append(f"- Пропущено (relevance): {processing.get('relevance_skipped', 0)}")
        lines.append(f"- Пропущено (другое): {processing['skipped']}")
        lines.append(f"- Ошибок: {processing['failed']}")
        lines.append("")

        digest_path.write_text("\n".join(lines), encoding="utf-8")
        return digest_path

    def _extract_tags(self, text: str) -> list[str]:
        """Извлекает теги из ответа LLM (по строке 'Теги: ...')."""
        tags: list[str] = []
        for line in text.splitlines():
            if line.strip().lower().startswith("теги"):
                parts = line.split(":", 1)
                if len(parts) == 2:
                    tags = [t.strip() for t in parts[1].split(",") if t.strip()]
                    break
        return tags[:10]

    def _guess_topic(self, text: str) -> str:
        """Определяет тематику заметки (заглушка, будет уточняться через профиль)."""
        lower = text.lower()
        if any(w in lower for w in ("python", "код", "программ", "разработк")):
            return "Программирование"
        if any(w in lower for w in ("автоматизац", "n8n", "скрипт")):
            return "Автоматизация"
        if any(w in lower for w in ("нейросет", "ии ", "гпт", "модель")):
            return "ИИ и нейросети"
        return "Общее"

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
