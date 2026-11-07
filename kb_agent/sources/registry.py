"""Реестр источников данных (Этап 5).

Каноническое хранилище — таблица `sources` в SQLite (через StateManager).
Файл sources.yaml используется только для импорта/экспорта и обратной
совместимости. CLI и MCP должны использовать один экземпляр SourceRegistry
с общим StateManager, чтобы избежать расхождения данных.
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml

from kb_agent.config import PROJECT_ROOT, AppConfig, load_config
from kb_agent.sources.base import SourceConfig
from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.sources.registry")

# Допустимые типы источников и паттерны их URL (P2-13).
SOURCE_TYPES = ("telegram", "youtube")

# Поддерживает: https://t.me/s/channel, https://t.me/channel, @channel, channel
_TELEGRAM_URL_RE = re.compile(
    r"^(?:https?://(?:www\.)?(?:t\.me|telegram\.me)/(?:s/)?)?@?([A-Za-z0-9_]{4,64})/?$"
)
_YOUTUBE_URL_RE = re.compile(
    r"^https?://(?:www\.)?youtube\.com/(?:@[A-Za-z0-9_.]+|c/[A-Za-z0-9_-]+|channel/[A-Za-z0-9_-]+)/?$"
)


def normalize_source(
    source_type: str, url: str, source_id: str | None = None
) -> tuple[str, str, str]:
    """Нормализует и валидирует источник (P2-13).

    Возвращает (source_id, url, source_type). source_id генерируется из URL,
    если не передан явно. Выбрасывает ValueError при некорректном типе или URL.
    """
    if source_type not in SOURCE_TYPES:
        raise ValueError(
            f"Неподдерживаемый тип источника: {source_type}. Доступны: {', '.join(SOURCE_TYPES)}."
        )

    url = url.strip()
    if source_type == "telegram":
        m = _TELEGRAM_URL_RE.match(url)
        if not m:
            raise ValueError(
                f"Неправильный URL Telegram-канала: {url}. "
                "Ожидается https://t.me/s/<канал>, https://t.me/<канал> или @<канал>."
            )
        channel_slug = m.group(1)
        canonical_url = f"https://t.me/s/{channel_slug}"
        resolved_id = source_id or f"tg_{channel_slug}"
        return resolved_id, canonical_url, source_type
    else:  # youtube
        if not _YOUTUBE_URL_RE.match(url):
            raise ValueError(
                f"Неправильный URL YouTube-канала: {url}. "
                f"Ожидается https://youtube.com/@<handle> или https://youtube.com/c/<канал>."
            )

        if not source_id:
            channel = url.rstrip("/").split("/")[-1].replace("@", "")
            source_id = f"yt_{channel}"
        return source_id, url, source_type


class SourceRegistry:
    """Реестр источников. SQLite — каноническое хранилище, YAML — только import/export."""

    def __init__(
        self,
        sources_path: Path | None = None,
        state: StateManager | None = None,
    ) -> None:
        if sources_path is None:
            config = load_config()
            sources_path = config.sources_path
        self.sources_path = Path(sources_path)
        self.state = state
        self._sources: dict[str, SourceConfig] = {}
        self._loaded = False
        self._use_sqlite = state is not None

    # ---- 내부: преобразование между SourceConfig и строкой SQLite ----

    def _row_to_config(self, row: dict) -> SourceConfig:
        return SourceConfig(
            id=row["source_id"],
            type=row["source_type"],
            url=row["channel"],
            enabled=bool(row["enabled"]),
            last_checked=row.get("updated_at"),
            last_updated=row.get("updated_at"),
        )

    def _config_to_row(self, cfg: SourceConfig) -> dict:
        return {
            "source_id": cfg.id,
            "source_type": cfg.type,
            "channel": cfg.url,
            "enabled": int(cfg.enabled),
        }

    # ---- Загрузка ----

    def load(self) -> dict[str, SourceConfig]:
        """Загружает источники из канонического хранилища (SQLite или YAML)."""
        if self._loaded and not self._sources:
            return self._sources

        if self._use_sqlite:
            self._load_from_sqlite()
        else:
            self._load_from_yaml()

        self._loaded = True
        logger.info("Загружено источников: %s", len(self._sources))
        return self._sources

    def _load_from_sqlite(self) -> None:
        if self.state is None:
            self._sources = {}
            return
        conn = self.state.connect()
        rows = conn.execute(
            "SELECT source_id, source_type, channel, enabled, updated_at FROM sources ORDER BY source_id"
        ).fetchall()
        self._sources = {row["source_id"]: self._row_to_config(dict(row)) for row in rows}

    def _load_from_yaml(self) -> None:
        if not self.sources_path.exists():
            logger.warning("Файл источников не найден: %s", self.sources_path)
            self._sources = {}
            return
        try:
            data = yaml.safe_load(self.sources_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            logger.error("Ошибка чтения sources.yaml: %s", exc)
            self._sources = {}
            return

        sources: dict[str, SourceConfig] = {}
        for item in data.get("sources", []) or []:
            try:
                cfg = SourceConfig.from_dict(item)
                sources[cfg.id] = cfg
            except Exception as exc:  # noqa: BLE001
                logger.warning("Пропуск некорректного источника %s: %s", item, exc)
        self._sources = sources

    # ---- Сохранение (YAML — только экспорт) ----

    def _atomic_write_yaml(self, path: Path, data: dict) -> None:
        """Атомарная запись YAML: во временный файл в той же директории, затем os.replace.

        Защита от частичной записи при параллельном доступе или падении процесса
        посередине записи — получатель либо видит старый файл, либо новый целиком.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        os.replace(tmp, path)

    def save(self) -> None:
        """Сохраняет источники в sources.yaml (экспорт, не каноническое хранилище)."""
        if self._use_sqlite:
            # SQLite уже обновлён через add/remove/set_enabled; YAML — только опциональный экспорт
            self._export_to_yaml()
            return
        data = {
            "version": 1,
            "sources": [cfg.to_dict() for cfg in self._sources.values()],
        }
        self._atomic_write_yaml(self.sources_path, data)
        logger.info("Сохранено источников: %s в %s", len(self._sources), self.sources_path)

    def _export_to_yaml(self) -> None:
        """Экспортирует источники из SQLite в sources.yaml (для ручного редактирования)."""
        try:
            data = {
                "version": 1,
                "sources": [cfg.to_dict() for cfg in self._sources.values()],
            }
            self._atomic_write_yaml(self.sources_path, data)
            logger.info("Экспортировано источников: %s в %s", len(self._sources), self.sources_path)
        except OSError as exc:
            logger.warning("Не удалось экспортировать sources.yaml: %s", exc)

    def import_from_yaml(self, sources_path: Path | None = None) -> int:
        """Импортирует источники из YAML-файла в SQLite (kanon). Возвращает количество импортированных."""
        path = Path(sources_path) if sources_path else self.sources_path
        if not path.exists():
            logger.warning("YAML файл не найден для импорта: %s", path)
            return 0
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            logger.error("Ошибка чтения YAML для импорта: %s", exc)
            return 0

        count = 0
        for item in data.get("sources", []) or []:
            try:
                cfg = SourceConfig.from_dict(item)
                self.add(cfg)
                count += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning("Пропуск некорректного источника при импорте %s: %s", item, exc)
        return count

    # ---- Чтение ----

    def list(self, enabled_only: bool = False) -> list[SourceConfig]:
        """Возвращает список источников."""
        self.load()
        result = list(self._sources.values())
        if enabled_only:
            result = [s for s in result if s.enabled]
        return sorted(result, key=lambda s: s.id)

    def get(self, source_id: str) -> SourceConfig | None:
        """Возвращает источник по id или None."""
        self.load()
        return self._sources.get(source_id)

    # ---- Запись (할я SQLite канонически) ----

    def add(self, source_config: SourceConfig) -> bool:
        """Добавляет источник. Возвращает False, если уже существует или некорректен."""
        self.load()
        # Нормализуем и валидируем тип + URL (P2-13).
        try:
            norm_id, norm_url, norm_type = normalize_source(
                source_config.type, source_config.url, source_config.id
            )
        except ValueError as exc:
            logger.error("Некорректный источник %s: %s", source_config.id, exc)
            return False
        source_config.id = norm_id
        source_config.url = norm_url
        source_config.type = norm_type

        if source_config.id in self._sources:
            logger.warning("Источник %s уже существует", source_config.id)
            return False

        if self._use_sqlite:
            conn = self.state.connect()
            try:
                conn.execute(
                    """
                    INSERT INTO sources (source_id, source_type, channel, enabled)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        source_config.id,
                        source_config.type,
                        source_config.url,
                        int(source_config.enabled),
                    ),
                )
                conn.commit()
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                logger.error("Ошибка добавления источника в SQLite %s: %s", source_config.id, exc)
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

        if self._use_sqlite:
            conn = self.state.connect()
            cur = conn.execute("DELETE FROM sources WHERE source_id = ?", (source_id,))
            conn.commit()
            if cur.rowcount == 0:
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

        if self._use_sqlite:
            conn = self.state.connect()
            cur = conn.execute(
                """
                UPDATE sources
                SET enabled = ?, updated_at = datetime('now')
                WHERE source_id = ?
                """,
                (int(enabled), source_id),
            )
            conn.commit()
            if cur.rowcount == 0:
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

        now = datetime.now(timezone.utc).isoformat()
        if checked:
            source.last_checked = now
        if updated:
            source.last_updated = now

        if self._use_sqlite:
            conn = self.state.connect()
            conn.execute(
                """
                UPDATE sources
                SET updated_at = datetime('now')
                WHERE source_id = ?
                """,
                (source_id,),
            )
            conn.commit()

        self.save()


def _sources_from_config(config: AppConfig) -> list[SourceConfig]:
    """Строит источники из переменных окружения KB_TELEGRAM_CHANNELS / KB_YOUTUBE_CHANNELS."""
    result: list[SourceConfig] = []

    for name in (config.telegram_channels or "").split(","):
        name = name.strip()
        if not name:
            continue
        channel = name if name.startswith("http") else f"https://t.me/s/{name}"
        slug = channel.rstrip("/").split("/")[-1].replace("@", "")
        result.append(SourceConfig(id=f"tg_{slug}", type="telegram", url=channel, enabled=True))

    for url in (config.youtube_channels or "").split(","):
        url = url.strip()
        if not url:
            continue
        slug = url.rstrip("/").split("/")[-1].replace("@", "")
        result.append(SourceConfig(id=f"yt_{slug}", type="youtube", url=url, enabled=True))

    return result


def ensure_sources_file(
    sources_path: Path | None = None,
    config: AppConfig | None = None,
) -> Path:
    """Создаёт sources.yaml, если файла нет.

    Приоритет: значения из окружения (`KB_TELEGRAM_CHANNELS`, `KB_YOUTUBE_CHANNELS`)
    → `config/sources.example.yaml` → пустой реестр.
    """
    if config is None or sources_path is None:
        loaded = load_config()
        config = config or loaded
        sources_path = sources_path or config.sources_path

    sources_path = Path(sources_path)
    if sources_path.exists():
        return sources_path

    sources_path.parent.mkdir(parents=True, exist_ok=True)

    env_sources = _sources_from_config(config)
    if env_sources:
        data = {"version": 1, "sources": [cfg.to_dict() for cfg in env_sources]}
        sources_path.write_text(
            yaml.dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        logger.info(
            "Создан %s из переменных окружения (%s источников)",
            sources_path,
            len(env_sources),
        )
        return sources_path

    example = PROJECT_ROOT / "config" / "sources.example.yaml"
    if example.exists():
        shutil.copy(example, sources_path)
        logger.info("Создан %s из %s", sources_path, example)
    else:
        sources_path.write_text("version: 1\nsources: []\n", encoding="utf-8")
        logger.info("Создан пустой %s", sources_path)
    return sources_path
