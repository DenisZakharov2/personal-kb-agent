"""Тесты логгирования (P2-08): rotating файловый обработчик."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from kb_agent.utils.logger import configure_file_logging, setup_logger


class TestSetupLogger:
    def test_setup_logger_creates_rotating_file_handler(self, tmp_path):
        """При log_path добавляется RotatingFileHandler (не FileHandler)."""
        log_path = tmp_path / "kb_agent.log"
        logger = setup_logger("kb_agent.test.rotate", log_path=log_path)

        file_handlers = [
            h for h in logger.handlers if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(file_handlers) == 1
        assert Path(file_handlers[0].baseFilename).resolve() == log_path.resolve()
        assert file_handlers[0].maxBytes == 5 * 1024 * 1024
        assert file_handlers[0].backupCount == 5
        # У логгера есть и консоль, и файл
        assert any(isinstance(h, logging.StreamHandler) for h in logger.handlers)

    def test_setup_logger_idempotent_no_duplicate_file_handler(self, tmp_path):
        """Повторный вызов setup_logger с тем же путём не дублирует обработчик."""
        log_path = tmp_path / "kb_agent.log"
        setup_logger("kb_agent.test.idem", log_path=log_path)
        setup_logger("kb_agent.test.idem", log_path=log_path)

        file_handlers = [
            h
            for h in logging.getLogger("kb_agent.test.idem").handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(file_handlers) == 1

    def test_setup_logger_without_log_path_no_file_handler(self):
        """Без log_path файлового обработчика нет."""
        logger = setup_logger("kb_agent.test.nofile")
        assert not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in logger.handlers)
        assert any(isinstance(h, logging.StreamHandler) for h in logger.handlers)


class TestConfigureFileLogging:
    def test_configure_file_logging_adds_handler_to_existing_loggers(self, tmp_path):
        """configure_file_logging добавляет файловый обработчик существующим логгерам."""
        log_path = tmp_path / "kb_agent.log"
        # Создаём логгер до настройки файлового логирования (как при импорте модуля)
        logger = setup_logger("kb_agent.test.cfg")
        assert not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in logger.handlers)

        configure_file_logging(log_path)

        updated = logging.getLogger("kb_agent.test.cfg")
        file_handlers = [
            h for h in updated.handlers if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(file_handlers) == 1
        assert Path(file_handlers[0].baseFilename).resolve() == log_path.resolve()

    def test_configure_file_logging_none_is_noop(self):
        """configure_file_logging(None) не падает и не ломает логгеры."""
        logger = setup_logger("kb_agent.test.none")
        handlers_before = len(logger.handlers)
        configure_file_logging(None)
        assert len(logger.handlers) == handlers_before
