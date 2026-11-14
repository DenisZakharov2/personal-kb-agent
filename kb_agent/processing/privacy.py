"""Политика приватности при работе с внешним LLM (раздел 6.2 документации).

Модуль реализует заявленные, но ранее не работавшие настройки:

- ``KB_LOCAL_PROCESSING_MODE=true`` — запрет отправки данных внешнему LLM;
  разрешены только локальные провайдеры (Ollama/локальный endpoint).
- ``KB_REDACT_FIELDS`` — список чувствительных полей (email, phone, canonical_url, …),
  которые вырезаются из текста запроса к внешнему LLM.
- ``KB_REDACT_SOURCES`` — список ``source_id``, контент которых не отправляется
  внешнему LLM вообще.

Локальные провайдеры (данные не покидают машину) не требуют ни redaction полей,
 ни исключения источников — иначе локальная база знаний теряла бы полезные данные.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - только для типизации
    from kb_agent.config import AppConfig

# Провайдеры, работающие локально (данные не покидают машину).
LOCAL_PROVIDERS = ("ollama", "local")

# Хосты, которые считаются локальными для провайдера «custom».
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal")

# Маркер, который вставляется вместо удалённого значения.
REDACTION_MARKER = "[REDACTED]"

# Регулярные выражения для известных имён полей.
_FIELD_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    "mail": re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    "phone": re.compile(r"(?<![\w])\+?\d[\d\s\-().]{6,}\d(?![\w])"),
    "телефон": re.compile(r"(?<![\w])\+?\d[\d\s\-().]{6,}\d(?![\w])"),
    "url": re.compile(r"https?://[^\s<>\"'`)\]]+"),
    "canonical_url": re.compile(r"https?://[^\s<>\"'`)\]]+"),
    "username": re.compile(r"(?<![\w@])@[A-Za-z0-9_]{4,}"),
    "telegram_id": re.compile(r"(?<![\w@])@[A-Za-z0-9_]{4,}"),
    "secret": re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}"),
    "token": re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{10,}"),
    "password": re.compile(r"(?i)\b(?:password|passwd|пароль)\b\s*[:=]\s*\S+"),
}


def _generic_field_pattern(name: str) -> re.Pattern[str]:
    """Резервный шаблон для неизвестного имени поля.

    Вырезается целиком строка, где поле упоминается («поле: значение»).
    """
    return re.compile(rf"(?im)^.*\b{re.escape(name)}\b.*$")


def _split_csv(value: str | None) -> tuple[str, ...]:
    """Разбирает список через запятую в кортеж нормализованных значений."""
    if not value:
        return ()
    return tuple(part.strip().lower() for part in value.split(",") if part.strip())


def is_loopback_url(base_url: str | None) -> bool:
    """Проверяет, что URL указывает на локальный хост."""
    if not base_url:
        return False
    from urllib.parse import urlparse

    try:
        host = (urlparse(base_url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    return host in LOOPBACK_HOSTS


@dataclass(frozen=True)
class Redactor:
    """Редактирование чувствительных данных перед отправкой во внешний LLM."""

    fields: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()

    @classmethod
    def from_config(cls, config: AppConfig) -> Redactor:
        """Собирает редактор из настроек приложения."""
        return cls(
            fields=_split_csv(getattr(config, "redact_fields", "")),
            sources=_split_csv(getattr(config, "redact_sources", "")),
        )

    @property
    def has_fields(self) -> bool:
        return bool(self.fields)

    @property
    def has_sources(self) -> bool:
        return bool(self.sources)

    def redact_text(self, text: str) -> str:
        """Вырезает чувствительные значения из текста.

        Для известных имён полей используются точные регулярные выражения,
        для неизвестных — резервный шаблон «строка с упоминанием поля».
        """
        if not text or not self.fields:
            return text
        result = text
        for name in self.fields:
            pattern = _FIELD_PATTERNS.get(name)
            if pattern is None:
                pattern = _generic_field_pattern(name)
            result = pattern.sub(f"{REDACTION_MARKER}:{name}", result)
        return result

    def is_source_redacted(self, source_id: str | None) -> bool:
        """Проверяет, что источник исключён политикой redaction."""
        if not source_id or not self.sources:
            return False
        return source_id.strip().lower() in self.sources


def is_local_provider(provider: str | None, base_url: str | None = None) -> bool:
    """Определяет, работает ли провайдер локально (данные не покидают машину)."""
    name = (provider or "").strip().lower()
    if name in LOCAL_PROVIDERS:
        return True
    if name == "custom":
        return is_loopback_url(base_url)
    return False
