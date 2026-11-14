"""Актуализация базы знаний (Этап 3).

Поиск дублей, конфликтов и предложение слияния заметок.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from kb_agent.config import AppConfig
from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.core.update")

# Порог схожести заголовков (Жаккар по нормализованным словам), при котором
# пара заметок с одинаковыми тегами считается кандидатом на дубликат.
TITLE_SIMILARITY_THRESHOLD = 0.6

# Ограничение на число пар, просматриваемых за один проход (защита от
# полного перебора на больших базах).
MAX_PAIRS_SCANNED = 2000

# Слова, которые не несут смысловой нагрузки при сравнении заголовков.
_TITLE_STOPWORDS = frozenset(
    {
        "и",
        "в",
        "во",
        "не",
        "что",
        "он",
        "на",
        "я",
        "с",
        "со",
        "как",
        "а",
        "то",
        "все",
        "она",
        "так",
        "его",
        "но",
        "да",
        "ты",
        "к",
        "у",
        "же",
        "вы",
        "за",
        "бы",
        "по",
        "только",
        "её",
        "мне",
        "было",
        "вот",
        "от",
        "меня",
        "для",
        "the",
        "a",
        "an",
        "of",
        "to",
        "and",
        "in",
        "is",
        "it",
    }
)

_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_SPACE_RE = re.compile(r"\s+", re.UNICODE)


def normalize_title(title: str | None) -> set[str]:
    """Нормализует заголовок в множество значимых слов.

    Регистр, пунктуация и служебные слова отбрасываются — так «Как купить
    квартиру?» и «купить квартиру» дают одинаковое множество слов.
    """
    if not title:
        return set()
    text = _PUNCT_RE.sub(" ", title.lower())
    words = {word for word in _SPACE_RE.split(text) if word and word not in _TITLE_STOPWORDS}
    return words


def title_similarity(title_1: str | None, title_2: str | None) -> float:
    """Схожесть двух заголовков по Жаккару (0.0 — 1.0)."""
    words_1 = normalize_title(title_1)
    words_2 = normalize_title(title_2)
    if not words_1 or not words_2:
        return 0.0
    union = words_1 | words_2
    if not union:
        return 0.0
    return len(words_1 & words_2) / len(union)


@dataclass
class DuplicatePair:
    """Пара похожих заметок, кандидаты на слияние."""

    note_id_1: str
    title_1: str
    note_id_2: str
    title_2: str
    similarity_reason: str


@dataclass
class UpdateReport:
    """Отчёт об актуализации базы знаний."""

    duplicates: list[DuplicatePair] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


class KnowledgeUpdater:
    """Сравнивает заметки и предлагает слияние дублей или обновление."""

    def __init__(self, config: AppConfig, state: StateManager) -> None:
        self.config = config
        self.state = state

    def check_duplicates(self, limit: int = 10) -> UpdateReport:
        """Ищет возможные дубликаты среди заметок.

        Критерий кандидата: одинаковый набор тегов **и** схожие заголовки
        (Жаккар по нормализованным словам ≥ ``TITLE_SIMILARITY_THRESHOLD``).
        Одних общих тегов недостаточно — иначе все заметки одной темы
        помечались бы как дубликаты.

        Выборка детерминирована (ORDER BY по note_id), поэтому результат
        не зависит от порядка строк в SQLite.
        """
        report = UpdateReport()

        conn = self.state.connect()
        rows = conn.execute(
            """
            SELECT
                n1.note_id AS note_id_1,
                n1.title AS title_1,
                n2.note_id AS note_id_2,
                n2.title AS title_2
            FROM notes_fts n1
            JOIN notes_fts n2 ON n1.note_id < n2.note_id
            WHERE n1.tags = n2.tags AND n1.tags != ''
            ORDER BY n1.note_id, n2.note_id
            LIMIT ?
            """,
            (MAX_PAIRS_SCANNED,),
        ).fetchall()

        for row in rows:
            if len(report.duplicates) >= limit:
                break
            score = title_similarity(row["title_1"], row["title_2"])
            if score < TITLE_SIMILARITY_THRESHOLD:
                continue
            report.duplicates.append(
                DuplicatePair(
                    note_id_1=row["note_id_1"],
                    title_1=row["title_1"] or "Без названия",
                    note_id_2=row["note_id_2"],
                    title_2=row["title_2"] or "Без названия",
                    similarity_reason=(f"Одинаковые теги и схожие заголовки ({score:.0%})"),
                )
            )

        if report.duplicates:
            report.suggestions.append(
                "Найдены возможные дубликаты. Рекомендуется проверить и объединить схожие заметки."
            )
        else:
            report.suggestions.append("Дубликатов не найдено.")

        return report

    def check_conflicts(self) -> UpdateReport:
        """Ищет противоречия между заметками (заглушка для будущего семантического анализа)."""
        report = UpdateReport()
        report.conflicts.append(
            "Семантический поиск противоречий будет доступен в следующей версии."
        )
        return report

    def merge_notes(self, keep_note_id: str, drop_note_id: str) -> dict:
        """Сервис слияния двух заметок: координирует состояние БД и файлы vault.

        Выполняет ``StateManager.merge_notes`` (БД) и
        ``VaultManager.merge_notes_files`` (файлы) последовательно. Возвращает
        объединённый отчёт.
        """
        from kb_agent.storage.vault import VaultManager

        state_report = self.state.merge_notes(keep_note_id, drop_note_id)

        vault = VaultManager(self.config)
        vault_report = vault.merge_notes_files(keep_note_id, drop_note_id)

        return {
            "kept_note_id": state_report["kept_note_id"],
            "dropped_note_id": state_report["dropped_note_id"],
            "links_moved": state_report["links_moved"],
            "raw_content_moved": state_report["raw_content_moved"],
            "fts_entries_removed": state_report["fts_entries_removed"],
            "vault": vault_report,
        }
