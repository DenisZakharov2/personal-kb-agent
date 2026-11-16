"""
Защита от prompt injection атак.

Модуль предоставляет функции для обнаружения и предотвращения
prompt injection атак при работе с LLM.
"""

from __future__ import annotations

import re

from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.utils.prompt_security")


# Паттерны для обнаружения prompt injection
INJECTION_PATTERNS = [
    # Попытки переопределить системный промпт
    (
        r"ignore\s+(all\s+)?(previous|above|prior)\s+(instructions?|prompts?|rules?|messages?)",
        "Попытка проигнорировать предыдущие инструкции",
    ),
    (
        r"forget\s+(all\s+)?(previous|above|prior)\s+(instructions?|prompts?|rules?|messages?)",
        "Попытка забыть предыдущие инструкции",
    ),
    (
        r"disregard\s+(all\s+)?(previous|above|prior)\s+(instructions?|prompts?|rules?|messages?)",
        "Попытка проигнорировать предыдущие инструкции",
    ),
    # Попытки смены ролей
    (r"\brole\s*[:=]\s*['\"]?system['\"]?", "Попытка изменить роль на system"),
    (r"\buser\s*[:=]\s*['\"]?system['\"]?", "Попытка подменить пользователя на system"),
    # Попытки выполнить код
    (r"execute\s+(code|command|script|bash|python|js|javascript)", "Попытка выполнить код"),
    (r"run\s+(code|command|script|bash|python)", "Попытка запустить код"),
    (r"eval\s*\(", "Попытка использовать eval"),
    (r"exec\s*\(", "Попытка использовать exec"),
    # Попытки доступа к системным файлам
    (
        r"read\s+(file|directory|folder)\s+['\"]?(/etc/passwd|/etc/shadow|\.env|\.bashrc|\.zshrc)['\"]?",
        "Попытка доступа к системным файлам",
    ),
    (
        r"access\s+(file|directory|folder)\s+['\"]?(/etc|/root|/home)['\"]?",
        "Попытка доступа к системным директориям",
    ),
]


class PromptScanner:
    """
    Сканер для обнаружения и нейтрализации prompt injection атак.
    """

    def __init__(self) -> None:
        self.patterns = INJECTION_PATTERNS

    def scan(self, text: str) -> tuple[bool, list[str]]:
        """
        Сканирует текст на наличие prompt injection паттернов.

        Args:
            text: Текст для сканирования

        Returns:
            Кортеж (is_safe, detected_patterns):
            - is_safe: True если текст безопасен, False если обнаружены атаки
            - detected_patterns: Список обнаруженных паттернов
        """
        detected_patterns: list[str] = []
        for pattern, description in self.patterns:
            if re.search(pattern, text, re.IGNORECASE):
                detected_patterns.append(description)

        return len(detected_patterns) == 0, detected_patterns

    def sanitize(self, text: str) -> tuple[str, list[str]]:
        """
        Очищает текст от потенциально опасного содержимого.

        Args:
            text: Текст для очистки

        Returns:
            Кортеж (sanitized_text, removed_patterns):
            - sanitized_text: Очищенный текст
            - removed_patterns: Список удаленных паттернов
        """
        removed_patterns: list[str] = []
        sanitized_text = text

        for pattern, description in self.patterns:
            if re.search(pattern, sanitized_text, re.IGNORECASE):
                # Заменяем найденные паттерны на безопасный текст
                sanitized_text = re.sub(
                    pattern, f"[REDACTED: {description}]", sanitized_text, flags=re.IGNORECASE
                )
                removed_patterns.append(description)

        return sanitized_text, removed_patterns


# Экземпляр сканера для импорта
prompt_scanner = PromptScanner()
