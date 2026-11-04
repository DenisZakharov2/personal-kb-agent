"""Логирование приложения (Этап 1).

Используется только стандартная библиотека logging (без loguru).
Чувствительные данные скрываются в логах.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

_SECRET_PATTERNS = [
    (re.compile(r"(sk-[A-Za-z0-9]{20,})"), "sk-***"),
    (re.compile(r"(sk-or-[A-Za-z0-9]{20,})"), "sk-or-***"),
    (re.compile(r"(eyJ[A-Za-z0-9_-]{20,})"), "jwt-***"),
    (re.compile(r"(password|passwd|pwd|secret|token|api_key|apikey)\s*[:=]\s*(\S+)"), r"\1=***"),
]


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


def setup_logger(name: str = "kb_agent", log_path: Path | None = None, level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False
    if logger.handlers:
        return logger
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    console_handler.addFilter(SecretFilter())
    logger.addHandler(console_handler)
    if log_path is not None:
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(fmt)
        file_handler.addFilter(SecretFilter())
        logger.addHandler(file_handler)
    return logger
