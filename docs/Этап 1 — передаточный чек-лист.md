# Этап 1 — передаточный чек-лист и инструкции для новой сессии

Проект: База знаний (personal-kb-agent)  
Репозиторий: https://github.com/DenisZakharov2  
Сессия: запуск Этапа 1 (основа, онбординг, импорт ядра) после Этапа 0.

## 1. Что утверждено на Этапе 0 (фундамент)

- **Режим:** локальный однопользовательский, один Obsidian vault, состояние только в SQLite (`data/state.db`).
- **Источники MVP:** Telegram + YouTube.
- **LLM-провайдер MVP:** OpenAI (OpenAI-совместимый HTTP API, клиент `httpx`). Один провайдер, без автоматического подбора.
- **Судьба скриптов `scripts/`:** `collect_telegram.py` и `collect_youtube.py` остаются референсом; логика оборачивается в `kb_agent/sources/` на Этапе 2; `run.sh` выводится из конвейера. Один поток, без дублей.
- **Схема данных:** таблицы SQLite `raw_queue`, `note_links`, `feedback`, `prompt_history`, `notes_fts` (FTS5); YAML-файлы `sources.yaml`, `user_profile.yaml`. Заметки — `.md` в vault.
- **Стек:** Python 3.10+, Pydantic v2, Typer + Rich, httpx, SQLite (встроенный `sqlite3` с FTS5), pytest, ruff.
- **Запрещено:** `python-dotenv`, `loguru`, `mypy`, `APScheduler`, `Jinja2`, `pytest-asyncio`, шесть HTTP-адаптеров агентов, микросервисы, внешняя БД, векторная БД, веб-интерфейс.
- **Лицензия:** MIT, но в `LICENSE` нужно добавить имя правообладателя (сейчас только `Copyright (c) 2026`).

Подробно: `docs/Этап 0 — уточнение scope и инвентаризация.md`.

## 2. Критерий готовности Этапа 0 (выполнено)

- [x] Локальный однопользовательский режим подтверждён
- [x] Источники MVP определены: Telegram + YouTube
- [x] LLM-провайдер MVP выбран: OpenAI; контракт `LLMClient` описан
- [x] Судьба скриптов `scripts/` зафиксирована
- [x] Схема данных утверждена (SQLite + YAML)
- [x] Набор материалов и вопросов для проверки качества подготовлен
- [x] LICENSE проверен (требуется добавить правообладателя)

## 3. Состав Этапа 1 (из утверждённого плана)

1. `pyproject.toml` с минимальными зависимостями (pydantic, typer, rich, httpx, pytest, ruff).
2. Пакет `kb_agent/` со структурой модулей (каталоги + `__init__.py`, без основной бизнес-логики на этом шаге).
3. `config.py` на Pydantic Settings (чтение `.env` и `config.yaml`).
4. `storage/state.py` — SQLite с таблицами: `raw_queue`, `notes_fts` (FTS5), `note_links`, `feedback`, `prompt_history`, `processed`; миграции SQLite.
5. `core/interview.py` — `InterviewManager`, команда `kb-agent interview init`, создание `user_profile.yaml`.
6. Импорт существующих материалов с `dry-run` и отчётом, сохранение оригиналов.
7. Логирование без утечки чувствительного содержимого.
8. Проверка/создание файла `LICENSE` (MIT, добавить правообладателя).

**Критерии готовности Этапа 1:**
- `pip install -e .` работает
- `kb-agent --version` выводит версию
- `kb-agent interview init` создаёт `user_profile.yaml`
- SQLite с FTS5 создаётся при первом запуске

## 4. Что НЕ делать на Этапе 1 (ограничения)

- Не писать основную бизнес-логику пакета `kb_agent/` (Source Layer, Processing Layer, LLMClient, Pipeline, KB Writer) — это Этап 2.
- Не добавлять новые фреймворки или библиотеки без обсуждения.
- Не реализовывать MCP-сервер, адаптеры источников, конвейер обработки.
- Не использовать запрещённые библиотеки из списка выше.

## 5. Шаги для новой сессии

1. Прочитать `docs/Этап 0 — уточнение scope и инвентаризация.md` и этот файл.
2. Запустить `scripts/legacy/init_stage1.sh` — подготовка окружения, зависимостей и структуры каталогов.
3. Проверить, что `pip install -e .` проходит и `kb-agent --version` работает.
4. Реализовать пункты 3–8 из раздела 3 (конфиг, SQLite, интервью, импорт, логирование, LICENSE).
5. Зафиксировать критерии готовности Этапа 1 и перейти к Этапу 2.

## 6. Файлы, созданные на Этапе 0

- `docs/Этап 0 — уточнение scope и инвентаризация.md` — зафиксированные решения
- `config/sources.example.yaml` — пример реестра источников
- `config/user_profile.example.yaml` — пример профиля пользователя
- `scripts/legacy/init_stage1.sh` — скрипт инициализации Этапа 1 (этот пакет)

## 7. Файлы репозитория, с которыми работать

- `README.md`, `LICENSE`, `.env.example`, `.gitignore`
- `config/channels.example.json` (удалён: не использовался кодом)
- `scripts/legacy/collect_telegram.py`, `scripts/legacy/collect_youtube.py`, `scripts/legacy/run.sh` (референс, не активный конвейер)
- `docs/architecture.md`, `docs/workflow.md`, `docs/youtube-transcripts.md`, `docs/catalog.md`
- `Пожелания к приложению для базы знаний.md`, `План реализации ИИ-агента.md`, `2Аудит плана реализации ИИ-агента.md`
