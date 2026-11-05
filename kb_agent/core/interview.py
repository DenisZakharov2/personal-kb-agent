"""Вводное и периодическое интервью пользователя (Этап 1).

Задаёт вопросы о целях, задачах, темах, источниках и сохраняет профиль
в user_profile.yaml. Интервью обязательно перед началом формирования базы знаний.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import yaml

from kb_agent.config import AppConfig


class InterviewManager:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.profile_path = config.profile_path

    _QUESTIONS: ClassVar[list[tuple[str, str]]] = [
        (
            "goal",
            "Какова цель базы знаний? (работа, учеба, саморазвитие, хобби)",
        ),
        (
            "tasks",
            "Какую задачу она должна решать для вас?",
        ),
        (
            "topics",
            "Какие темы вас интересуют? (через запятую)",
        ),
        (
            "sources",
            "Какие источники будут использоваться? (Telegram, YouTube, сайты, свои материалы)",
        ),
        (
            "frequency",
            "С какой частотой собирать информацию? (раз в день, неделю, месяц)",
        ),
        (
            "system",
            "В какой системе вы будете работать с базой знаний? (по умолчанию Obsidian)",
        ),
        (
            "core_materials",
            "Есть ли уже готовые материалы для ядра базы? (путь к папке или 'нет')",
        ),
    ]

    def ask(self, question: str, default: str | None = None) -> str:
        from rich.prompt import Prompt
        prompt_text = question
        if default:
            prompt_text += f" [{default}]"
        return Prompt.ask(prompt_text, default=default)

    def run_init_interview(self) -> dict:
        print("\n=== Вводное интервью базы знаний ТГК ===")
        print("Ответьте на несколько вопросов, чтобы настроить базу под себя.\n")
        profile: dict = {}
        for key, question in self._QUESTIONS:
            answer = self.ask(question)
            if answer.strip():
                profile[key] = answer.strip()
        if "topics" in profile:
            profile["topics"] = [t.strip() for t in profile["topics"].split(",") if t.strip()]
        if "sources" in profile:
            profile["sources"] = [s.strip() for s in profile["sources"].replace(";", ",").split(",") if s.strip()]
        profile["system"] = profile.get("system", "Obsidian")
        profile["vault_path"] = str(self.config.vault_path)
        return profile

    def save_profile(self, profile: dict) -> Path:
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "user": profile}
        self.profile_path.write_text(
            yaml.dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return self.profile_path

    def load_profile(self) -> dict | None:
        if not self.profile_path.exists():
            return None
        try:
            data = yaml.safe_load(self.profile_path.read_text(encoding="utf-8"))
            return data.get("user") if isinstance(data, dict) else None
        except Exception:
            return None

    def profile_exists(self) -> bool:
        return self.profile_path.exists()
