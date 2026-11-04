"""Менеджер опроса источников данных (Этап 5).

Берёт активные источники из SourceRegistry, опрашивает каждый через
соответствующий сборщик и пишет новые элементы в raw_queue SQLite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from kb_agent.config import AppConfig, load_config
from kb_agent.sources.registry import SourceRegistry, SourceConfig
from kb_agent.sources.telegram import collect_telegram_source
from kb_agent.sources.youtube import collect_youtube_source
from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.sources.manager")


@dataclass
class SourceCollectionResult:
    """Результат сбора из всех источников."""

    total_new: int = 0
    total_spam: int = 0
    total_skipped: int = 0
    total_with_subtitles: int = 0
    by_source: dict[str, dict] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class SourceManager:
    """Опрашивает все активные источники и пишет в очередь."""

    def __init__(self, config: AppConfig, state: StateManager) -> None:
        self.config = config
        self.state = state
        registry_path = config.sources_path
        self.registry = SourceRegistry(registry_path)

    def collect_all(self, limit: Optional[int] = None) -> SourceCollectionResult:
        """Опрашивает все включённые источники."""
        limit = limit or self.config.limit
        self.registry.load()
        sources = self.registry.list(enabled_only=True)
        result = SourceCollectionResult()

        for source in sources:
            try:
                stats = self._collect_one(source, limit)
                result.by_source[source.id] = stats
                result.total_new += stats.get("new", 0)
                result.total_spam += stats.get("spam", 0)
                result.total_skipped += stats.get("skipped", stats.get("existing", 0))
                result.total_with_subtitles += stats.get("with_subtitles", 0)
                self.registry.touch(source.id, checked=True, updated=stats.get("new", 0) > 0)
            except Exception as exc:  # noqa: BLE001
                logger.error("Ошибка сбора из источника %s: %s", source.id, exc)
                result.errors.append(f"{source.id}: {exc}")
                result.by_source[source.id] = {"error": str(exc)}
                self.registry.touch(source.id, checked=True, updated=False)

        return result

    def collect_one(self, source_id: str, limit: Optional[int] = None) -> dict:
        """Опрашивает один источник по id."""
        limit = limit or self.config.limit
        self.registry.load()
        source = self.registry.get(source_id)
        if not source:
            raise ValueError(f"Источник не найден: {source_id}")
        if not source.enabled:
            raise ValueError(f"Источник выключен: {source_id}")
        return self._collect_one(source, limit)

    def _collect_one(self, source: SourceConfig, limit: int) -> dict:
        """Опрашивает один источник в зависимости от типа."""
        if source.type == "telegram":
            return collect_telegram_source(
                self.state,
                source_id=source.id,
                channel=source.url,
                limit=limit,
            )
        if source.type == "youtube":
            return collect_youtube_source(
                self.state,
                source_id=source.id,
                channel_url=source.url,
                months=self.config.yt_months,
                sub_langs=self.config.yt_sub_langs,
                limit=limit,
            )
        raise ValueError(f"Неподдерживаемый тип источника: {source.type}")


def run_collection(config: Optional[AppConfig] = None, state: Optional[StateManager] = None) -> SourceCollectionResult:
    """Удобная точка входа для сбора из всех источников."""
    if config is None:
        config = load_config()
    if state is None:
        state = StateManager(config.db_path)
        state.init_db()
        created_here = True
    else:
        created_here = False
    try:
        manager = SourceManager(config, state)
        return manager.collect_all()
    finally:
        if created_here:
            state.close()
