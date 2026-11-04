"""Базовый класс для источников данных (Этап 2/5).

Каждый источник наследуется от BaseSource и реализует fetch() — получение
сырых материалов из конкретного канала/сайта.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SourceConfig:
    """Конфигурация одного источника."""

    id: str
    type: str  # telegram, youtube, website, papers, personal
    url: str
    enabled: bool = True
    last_checked: Optional[str] = None
    last_updated: Optional[str] = None
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "SourceConfig":
        return cls(
            id=data["id"],
            type=data["type"],
            url=data["url"],
            enabled=data.get("enabled", True),
            last_checked=data.get("last_checked"),
            last_updated=data.get("last_updated"),
            extra={k: v for k, v in data.items() if k not in ("id", "type", "url", "enabled", "last_checked", "last_updated")},
        )

    def to_dict(self) -> dict:
        data: dict = {
            "id": self.id,
            "type": self.type,
            "url": self.url,
            "enabled": self.enabled,
        }
        if self.last_checked is not None:
            data["last_checked"] = self.last_checked
        if self.last_updated is not None:
            data["last_updated"] = self.last_updated
        data.update(self.extra)
        return data


@dataclass
class SourceItem:
    """Один собранный элемент из источника."""

    source_id: str
    external_id: str
    canonical_url: str
    title: str
    raw_text: str
    raw_hash: str
    published_at: Optional[str] = None


class BaseSource(ABC):
    """Абстрактный источник данных."""

    def __init__(self, source_config: SourceConfig) -> None:
        self.config = source_config

    @abstractmethod
    def fetch(self, limit: int) -> list[SourceItem]:
        """Собирает элементы из источника. Возвращает список SourceItem."""
        ...

    @property
    @abstractmethod
    def source_type(self) -> str:
        """Тип источника (telegram, youtube, ...)."""
        ...
