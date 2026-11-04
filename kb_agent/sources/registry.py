"""Реестр источников данных (Этап 5).

Управляет файлом sources.yaml — единственным источником истины для
конфигурации источников. Состояние приложения (очередь, заметки, связи)
хранится только в SQLite; sources.yaml — только конфиг.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

import yaml

from kb_agent.config import load_config, DEFAULT_SOURCES_PATH, PROJECT_ROOT
from kb_agent.sources.base import SourceConfig
from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.sources.registry")


class SourceRegistry:
    """Реестр источников на базе sources.yaml."""

    def __init__(self, sources_path: Optional[Path] = None) -> None:
        if sources_path is None:
            config = load_config()
            sources_path = config.sources_path
        self.sources_path = Path(sources_path)
        self._sources: dict[str, SourceConfig] = {}
        self._loaded = False

    def load(self) -> dict[str, SourceConfig]:
        """Загружает источники из sources.yaml."""
        if self._loaded and not self._sources:
            # уже пытались загрузить, файл пуст/отсутствует
            return self._sources
        if not self.sources_path.exists():
            logger.warning("Файл источников не найден: %s", self.sources_path)
            self._sources = {}
            self._loaded = True
            return self._sources
        try:
            data = yaml.safe_load(self.sources_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            logger.error("Ошибка чтения sources.yaml: %s", exc)
            self._sources = {}
            self._loaded = True
            return self._sources

        sources: dict[str, SourceConfig] = {}
        for item in data.get("sources", []) or []:
            try:
                cfg = SourceConfig.from_dict(item)
                sources[cfg.id] = cfg
            except Exception as exc:  # noqa: BLE001
                logger.warning("Пропуск некорректного источника %s: %s", item, exc)
        self._sources = sources
        self._loaded = True
        logger.info("Загружено источников: %s", len(sources))
        return self._sources

    def save(self) -> None:
        """Сохраняет источники в sources.yaml."""
        self.sources_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "sources": [cfg.to_dict() for cfg in self._sources.values()],
        }
        self.sources_path.write_text(
            yaml.dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        logger.info("Сохранено источников: %s в %s", len(self._sources), self.sources_path)

    def list(self, enabled_only: bool = False) -> list[SourceConfig]:
        """Возвращает список источников."""
        self.load()
        result = list(self._sources.values())
        if enabled_only:
            result = [s for s in result if s.enabled]
        return sorted(result, key=lambda s: s.id)

    def get(self, source_id: str) -> Optional[SourceConfig]:
        """Возвращает источник по id или None."""
        self.load()
        return self._sources.get(source_id)

    def add(self, source_config: SourceConfig) -> bool:
        """Добавляет источник. Возвращает False, если уже существует."""
        self.load()
        if source_config.id in self._sources:
            logger.warning("Источник %s уже существует", source_config.id)
            return False
        self._sources[source_config.id] = source_config
        self.save()
        logger.info("Источник добавлен: %s (%s)", source_config.id, source_config.type)
        return True

    def remove(self, source_id: str) -> bool:
        """Удаляет источник. Возвращает False, если не найден."""
        self.load()
        if source_id not in self._sources:
            logger.warning("Источник %s не найден", source_id)
            return False
        del self._sources[source_id]
        self.save()
        logger.info("Источник удалён: %s", source_id)
        return True

    def set_enabled(self, source_id: str, enabled: bool) -> bool:
        """Включает или выключает источник."""
        self.load()
        source = self._sources.get(source_id)
        if not source:
            logger.warning("Источник %s не найден", source_id)
            return False
        source.enabled = enabled
        self.save()
        logger.info("Источник %s: enabled=%s", source_id, enabled)
        return True

    def touch(self, source_id: str, checked: bool = True, updated: bool = False) -> None:
        """Обновляет метрики времени проверки/обновления источника."""
        self.load()
        source = self._sources.get(source_id)
        if not source:
            return
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        if checked:
            source.last_checked = now
        if updated:
            source.last_updated = now
        self.save()


def ensure_sources_file(sources_path: Optional[Path] = None) -> Path:
    """Создаёт data/sources.yaml из примера, если файла нет."""
    if sources_path is None:
        config = load_config()
        sources_path = config.sources_path
    sources_path = Path(sources_path)
    if sources_path.exists():
        return sources_path

    example = PROJECT_ROOT / "config" / "sources.example.yaml"
    if example.exists():
        sources_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(example, sources_path)
        logger.info("Создан %s из %s", sources_path, example)
    else:
        sources_path.parent.mkdir(parents=True, exist_ok=True)
        sources_path.write_text("version: 1\nsources: []\n", encoding="utf-8")
        logger.info("Создан пустой %s", sources_path)
    return sources_path
