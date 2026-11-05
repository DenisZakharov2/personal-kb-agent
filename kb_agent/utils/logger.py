"""Логирование приложения (Этап 1).

Используется только стандартная библиотека logging (без loguru).
Чувствительные данные скрываются в логах. Поддерживается ротация файлового
журнала (P2-08).
"""

from __future__ import annotations

import logging
import logging.handlers
import re
from pathlib import Path

_SECRET_PATTERNS = [
    (re.compile(r"(sk-[A-Za-z0-9]{20,})"), "sk-***"),
    (re.compile(r"(sk-or-[A-Za-z0-9]{20,})"), "sk-or-***"),
    (re.compile(r"(eyJ[A-Za-z0-9_-]{20,})"), "jwt-***"),
    (re.compile(r"(password|passwd|pwd|secret|token|api_key|apikey)\s*[:=]\s*(\S+)"), r"\1=***"),
]

_LOGGER_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
# ローテーション: до 5 МБ на файл, хранить до 5 файлов.
_LOG_MAX_BYTES = 5 * 1024 * 1024
_LOG_BACKUP_COUNT = 5


def mask_secrets(text: str) -> str:
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.msg:
            record.msg = mask_secrets(str(record.msg))
        if record.args:
            record.args = tuple(mask_secrets(str(a)) for a in record.args)
        return True


def _has_console_handler(logger: logging.Logger) -> bool:
    return any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.handlers.RotatingFileHandler)
        for h in logger.handlers
    )


def _has_file_handler(logger: logging.Logger, path: Path) -> bool:
    resolved = path.resolve()
    return any(
        isinstance(h, logging.handlers.RotatingFileHandler)
        and Path(h.baseFilename).resolve() == resolved
        for h in logger.handlers
    )


def _make_formatter() -> logging.Formatter:
    return logging.Formatter(_LOGGER_FORMAT)


def setup_logger(
    name: str = "kb_agent",
    log_path: Path | None = None,
    level: int = logging.INFO,
) -> logging.Logger:
    """Создаёт/настраивает логгер с консолью и, по желанию, rotating файлом.

    Вызывается повторно безопасно: консольный и файловый обработчики
    добавляются по одному разу.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    fmt = _make_formatter()

    if not _has_console_handler(logger):
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(fmt)
        console_handler.addFilter(SecretFilter())
        logger.addHandler(console_handler)

    if log_path is not None:
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if not _has_file_handler(logger, log_path):
            file_handler = logging.handlers.RotatingFileHandler(
                log_path,
                maxBytes=_LOG_MAX_BYTES,
                backupCount=_LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setFormatter(fmt)
            file_handler.addFilter(SecretFilter())
            logger.addHandler(file_handler)
    return logger


def configure_file_logging(log_path: Path | None = None, level: int = logging.INFO) -> None:
    """Подключает rotating файловый журнал ко всем существующим логгерам kb_agent.

    Вызывается после загрузки конфигурации (когда известен config.log_path).
    Модульные логгеры (создаются при импорте) уже имеют консольный обработчик;
    здесь добавляется общий файловый обработчик.
    """
    if log_path is None:
        return
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    names = {"kb_agent"} | {
        name for name in logging.root.manager.loggerDict if name == "kb_agent" or name.startswith("kb_agent.")
    }
    for name in names:
        setup_logger(name, log_path=log_path, level=level)
