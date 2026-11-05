"""Конфигурация источников данных и العناصر сбора (Этап 2/5).

SourceConfig описывает один источник (тип, URL, флаг enabled). SourceItem —
один собранный элемент из источника.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SourceConfig:
    """Конфигурация одного источника."""

    id: str
    type: str  # telegram, youtube, website, papers, personal
    url: str
    enabled: bool = True
    last_checked: str | None = None
    last_updated: str | None = None
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> SourceConfig:
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
    published_at: str | None = None
