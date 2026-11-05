# Аудиторский отчет: personal-kb-agent

**Дата аудита:** 2026-11-05  
**Репозиторий:** https://github.com/DenisZakharov2/personal-kb-agent  
**Версия:** 0.2.0 (pyproject.toml)  
**Аудитор:** AI-ассистент

---

## 📋 Executive Summary

Репозиторий представляет собой локальный ИИ-агент для базы знаний в Obsidian с поддержкой MCP. Проект имеет **хорошую архитектуру**, **широкое покрытие тестами** (38 файлов, 297 тестов) и **подробную документацию**. Однако выявлены **критические и высокоприоритетные проблемы** в области безопасности, CI/CD, конфигурации, обработке ошибок и документации.

**Общий уровень готовности:** **Beta (как указано в классификаторе)**, но требует исправления P0/P1 проблем перед продакшен-использованием.

---

## 🔴 Критические проблемы (P0 — Блокеры безопасности и работоспособности)

### 1. **Инвертированная маркировка `untrusted_content` в MCP** 
**Файл:** `kb_agent/agents/mcp_server.py` (строки ~823, ~1192, ~1258)

**Проблема:** Метод `_is_url_allowed()` проверяет, что URL принадлежит allowlist (t.me, youtube.com), и возвращает `True` для разрешенных доменов. Этот же метод используется для вычисления флага `untrusted_content = not _is_url_allowed(url)`. **Результат:** заметки из Telegram и YouTube (все они имеют canonical_url на этих доменах) получают `untrusted_content=False`, хотя это классический вектор **Indirect Prompt Injection**.

**Как исправить:**
```python
# Разделить понятия:
# 1. _is_source_url_allowed() — для добавления источника (allowlist)
# 2. untrusted_content = bool(canonical_url) — любой внешний контент = untrusted
```

**Почему критично:** Противоречит разделу 6.1 README, вводит в заблуждение клиентских агентов, открывает вектор атак через вредоносные посты в публичных каналах.

---

### 2. **Неработоспособность CI-воркфлоу сбора** (`.github/workflows/collect.yml`)

**Проблемы:**
1. **Проброс секретов LLM исправлен** (добавлены `KB_OPENAI_API_KEY`, `KB_OPENAI_BASE_URL`, `KB_OPENAI_MODEL`, `KB_LLM_PROVIDER`) ✅
2. **Отсутствует восстановление `state.db` между запусками по расписанию:** каждый cron-запуск работает с пустой базой — дедупликация не работает, накапливаются дубли.
3. **Путь `KB_VAULT_PATH` из секрета** может не существовать на раннере (Permission denied).
4. **Артефакты не сохраняют полное состояние:** vault копируется только `.md` файлы, но не структура папок.

**Как исправить:**
- Использовать эфемерные пути на раннере (без секрета `KB_VAULT_PATH`)
- Восстанавливать `state.db` **перед** запуском, сохранять **после** (уже есть, но проверить полноту)
- Добавить `actions/cache` для ускорения или коммитить в отдельную ветку `kb-data`

---

### 3. **Подавление ошибок сборщиков (нарушение контракта P2-14)**  
**Файлы:** `kb_agent/sources/telegram.py` (строки 52-54), `kb_agent/sources/youtube.py` (строки 46-51, 258)

**Проблема:** Сетевые ошибки, таймауты, ошибки `yt-dlp` перехватываются внутри функций и возвращают `[]`. `SourceManager` не видит ошибку, помечает источник как `healthy`, CLI возвращает exit code `0`.

**Как исправить:** Пробрасывать `RuntimeError`/`CollectionError` вверх до `SourceManager.collect_all()`, чтобы:
- `collection.errors` заполнялся корректно
- Health-check показывал `failed`
- Exit code `kb-agent process run` был `1` при любом сбое сбора

---

### 4. **Сбор старых постов вместо новых в Telegram**  
**Файл:** `kb_agent/sources/telegram.py` (строка 60)

**Проблема:** На странице `https://t.me/s/<channel>` посты идут хронологически (сверху старые, снизу новые). Код берет `msg_widgets[:limit]` — **10 самых старых** постов из ленты (~20 постов). Новые игнорируются.

**Как исправить:** `msg_widgets[-limit:]` (последние N элементов) + пропуск пустых постов `if not text.strip(): continue`.

---

### 5. **Ошибки в YouTube-сборщике**  
**Файл:** `kb_agent/sources/youtube.py`

| Строка | Проблема | Последствие |
|--------|----------|-------------|
| 183 | `DEFAULT_SUB_LANGS = "ru,en"` → `[l.strip() for l in sub_langs.split(",")] or [DEFAULT_SUB_LANGS]` даёт `["ru,en"]` (1 элемент) | Субтитры не скачиваются |
| 222-237 | `raw_hash` считается по `title + description` **до** добавления `transcript_text` | Изменения субтитров не детектятся как новый контент |
| 258 | `mark_processed_key(external_id)` вызывается **сразу после enqueue**, до суммаризации | При падении LLM видео помечается обработанным и теряется навсегда |
| 42 | `yt-dlp` без `--playlist-end` выгружает все видео канала за все годы | Медленно, лишняя нагрузка |

**Как исправить:**
- `DEFAULT_SUB_LANGS.split(",")` 
- `raw_hash` после сборки полного текста (включая субтитры)
- `mark_processed_key` перенести в `Pipeline` после успешного создания заметки
- Добавить `--playlist-end {max(limit * 3, 30)}`

---

### 6. **Пропуск проверки TTL (`expires_at`) при подтверждении pending-изменений**  
**Файл:** `kb_agent/storage/pending.py` (метод `confirm_change`, строки 116-165)

**Проблема:** Метод проверяет `status`, `revision`, `content_hash`, но **не проверяет `expires_at`**. Просроченный запрос агента может быть применен в любой момент.

**Как исправить:** В начале транзакции `confirm_change`:
```python
if change.get("expires_at") and change["expires_at"] <= now_iso:
    # пометить expired, вернуть False
```

---

## 🟠 Высокоприоритетные проблемы (P1 — Логика ядра, хранение, сборщики)

### 7. **RelevanceGate — остаточные риски после исправлений**  
**Файл:** `kb_agent/core/relevance.py`

**Статус:** Основные фиксы применены (STOP_WORDS, SPAM_PATTERNS с границами слов, кириллическая `а` в regex). Остаются нюансы:
- Порог `DEFAULT_THRESHOLD = 0.3` может быть низким для коротких постов
- `_coverage` считает долю токенов темы в тексте, но не учитывает tf-idf веса — общие слова типа "работа", "код" дают ложные срабатывания
- Нет логирования принятых/отклоненных материалов для аудита порога

**Рекомендация:** Добавить конфигурируемый `min_topic_tokens` (мин. токенов темы для участия в скоринге) и метрики `relevance_accepted/rejected` в дайджест.

---

### 8. **Некорректный приоритет загрузки `.env` в `config.py`**  
**Файл:** `kb_agent/config.py` (строки 180-186, функция `load_config`)

**Проблема:** Явный `env_file` читается **перед** стандартным `.env`, но затем `.env` **перезаписывает** его значения:
```python
if env_file: env_vars.update(_read_env_file(Path(env_file)))  # 1-й слой
default_env = PROJECT_ROOT / ".env"
if default_env.exists(): env_vars.update(_read_env_file(default_env))  # 2-й слой — ПЕРЕЗАПИСЫВАЕТ!
```
Переменные окружения (3-й слой) имеют приоритет, но `env_file` теряет смысл.

**Как исправить:** Сначала `default_env`, потом `env_file`, потом `os.environ`.

---

### 9. **Ошибка протокола контекстного менеджера в `_resource_path`**  
**Файл:** `kb_agent/config.py` (строки 69-75)

**Проблема:** 
```python
with importlib_resources.files(package).joinpath(resource_name) as ref:
    return Path(ref)
```
`importlib.resources.files().joinpath()` возвращает `Traversable`, а не контекстный менеджер. В Python 3.13+ это вызовет `TypeError`. В wheel временный файл удаляется при выходе из `with`.

**Как исправить:** Удалить `_resource_path`, использовать `read_resource_text()` напрямую (уже есть и работает).

---

### 10. **Игнорирование `KB_PROMPTS_DIR` / `config.prompts_dir`**  
**Файл:** `kb_agent/config.py` (функция `get_prompt_text`, строки 260-290)

**Проблема:** Функция сначала читает из `kb_agent.resources` (упакованные в wheel), поэтому пользовательские файлы в `KB_PROMPTS_DIR` **никогда не читаются**.

**Как исправить:** Порядок приоритета:
1. Пользовательский файл в `config.prompts_dir` (если передан `config`)
2. Упакованный ресурс `kb_agent.resources` (fallback для wheel)
3. Файл в `config/prompts/` (dev режим)

---

### 11. **Парсер `_read_env_file` не обрабатывает inline-комментарии и `export `**  
**Файл:** `kb_agent/config.py` (строки 46-65)

**Проблема:** Строка `KB_LIMIT=10 # лимит` парсится как `"10 # лимит"` → ошибка валидации Pydantic `int`. Префикс `export KB_LIMIT=10` тоже не срезается.

**Как исправить:** В `_read_env_file` срезать `export ` в начале и комментарии `#` (не в кавычках).

---

### 12. **Несовпадение заголовков между Vault и SQLite FTS5**  
**Файл:** `kb_agent/processing/pipeline.py` (строки 206-257, 298-304)

**Проблема:**
- В Vault записывается `summary.title` (сгенерированный LLM)
- В `state.index_note()` передается `item["title"]` (сырые первые 100 символов поста)
- **Результат:** В файле заметки и `Каталог.md` — один заголовок, в поиске (`kb-agent search`, MCP `kb_search`) — другой.

**Как исправить:** Использовать `final_title = summary.title or item["title"] or "Без названия"` **оба места**.

---

### 13. **Ошибки в генераторе каталога и VaultManager**  
**Файлы:** `kb_agent/storage/catalog.py`, `kb_agent/storage/vault.py`

| Проблема | Локация | Последствие |
|----------|---------|-------------|
| `rebuild_catalog(vault_path: Path)` передает `Path` вместо `AppConfig` в `VaultManager()` | `catalog.py:126` | `AttributeError: 'PosixPath' object has no attribute 'vault_path'` |
| `fm.get("topic")` может вернуть `None` → `sorted(None)` → `TypeError` | `catalog.py:95-112` | Падение генерации каталога |
| `list_notes()` возвращает архивные заметки (`Архив/`) | `catalog.py:95` | Архивные дубли попадают в `Каталог.md` |
| `_rewrite_frontmatter` пишет без `encoding="utf-8"` и без `atomic_write_text` | `vault.py:256` | На Windows портит UTF-8 (cp1251) |

**Как исправить:**
- `VaultManager.__init__` принимать `Union[AppConfig, Path, str]`
- `str(fm.get("topic") or "Общее")`, пропускать `archived=True` и папку `Архив`
- Использовать `atomic_write_text(path, ..., backup=False)` с `encoding="utf-8"`

---

### 14. **Опечатки и мертвый код в pipeline.py**  
**Файл:** `kb_agent/processing/pipeline.py`

1. **Дайджест:** `relevance_skipped` и `skipped` увеличиваются одновременно → в `Дайджест.md` одно число дважды, `updated` не выводится.
2. **Мертвые методы:** `_extract_tags()` и `_extract_topic()` (строки 308-334) не используются после перехода на `summarize_structured()`.

**Как исправить:** 
- `other_skipped = processing['skipped'] - processing.get('relevance_skipped', 0)`
- Выводить `updated`
- Удалить мертвые методы

---

### 15. **Противоречия между `config/` и `kb_agent/resources/`**  
**Файлы:** шаблоны в двух местах

**Статус:** На момент аудита файлы **синхронизированы** (проверено: `sources.example.yaml`, `user_profile.example.yaml`, `prompts/agent_prompt.md` идентичны). Но **риск рассинхрона** остается — нет CI-проверки.

**Как исправить:** Добавить в `quality.yml` step:
```bash
diff -ru config/ kb_agent/resources/ || (echo "Рассинхрон шаблонов!" && exit 1)
```
Или оставить только `kb_agent/resources/` как каноническое место, в `config/` — симлинки.

---

### 16. **Ошибки разметки и несостыковки в документации**  

| Файл | Проблема |
|------|----------|
| `README.md` (строки 275-310) | Пропущен ```` ```bash ```` перед командами `self-improve` → комментарии рендерятся как `<h1>`, лишние блоки кода |
| `SKILL.md` (строка 55) | Оборванная строка: `...модель не может подт` |
| `SKILL.md` | Нет YAML frontmatter (`--- name: kb-agent ... ---`) для парсинга агентскими платформами |
| `.env.example` (строка 62) | Упоминание несуществующего инструмента `kb_confirm_change` |
| `.cursorrules` (строки 25, 30) | Личный путь разработчика `/home/anony/...`, упоминание `kb_confirm_change` |
| `README.md` (строка 265) | Несуществующая команда `kb-agent changes confirm <id>` (есть только `kb-agent confirm <id>`) |
| Документация MCP | Не задокументированы 4 инструмента: `kb_sources_status`, `kb_list_proposals`, `kb_get_proposal`, `kb_reject_proposal` |
| `.env.example` | Отсутствуют `KB_LOCAL_PROCESSING_MODE`, `KB_REDACT_FIELDS`, `KB_REDACT_SOURCES` |

---

### 17. **Проблемы управления зависимостями**  
**Файл:** `pyproject.toml`

1. **`yt-dlp` в optional extra `[youtube]`** (`yt-dlp>=2024.08,<2026`):
   - В README `pip install -e .` без `[youtube]` → `yt-dlp` не ставится
   - Верхняя граница `<2026` в 2026 году блокирует актуальные версии (YouTube ломает старые `yt-dlp`)
2. **Дублирование HTTP-клиентов:** `httpx` + `requests` (используется только в `telegram.py` для одного GET)
3. **Нет проверки `uv.lock` в CI** (файл есть в корне, но `quality.yml` его не валидирует)

**Как исправить:**
- Перенести `yt-dlp` в основные `dependencies` (YouTube — основная фича) или обновить README
- Снять `<2026` или использовать `>=2024.08`
- Заменить `requests.get` на `httpx.get` в `telegram.py`
- Добавить `uv lock --check` в `quality.yml`

---

### 18. **Неполная проверка в CI (`quality.yml`)**  
- `ruff check kb_agent scripts tests` — **нет `tests/` в аргументах** (передается, но проверить)
- `mypy kb_agent scripts tests` — добавить `--strict` (уже в `pyproject.toml`, но явно не повредит)
- Нет проверки `uv.lock` целостности

---

### 19. **Опечатки в именах файлов и гигиена репозитория**  
**Корень репозитория:**
- `Пожелания ка приложению длябазы знаний.md` — опечатки: `ка` вместо `к`, `длябазы` слитно
- `Аудит плана реализации ИИ-агента.md`, `План реализации ИИ-агента.md` — проектные артефакты в корне с пробелами и кириллицей в URL
- `.env.save` в истории коммитов (`65174af`) — требует `git filter-repo` для очистки от GitHub Secret Scanning

**scripts/:** Дубли `collect_telegram.py` и `collect_youtube.py` в `scripts/` и `scripts/legacy/`

**GitHub About:** Topics = 0 (хотя в `pyproject.toml` отличные keywords)

---

## 🟡 Среднеприоритетные проблемы (P2 — UX, качество кода, документация)

### 20. **Отсутствие английской версии README**  
Проект на GitHub без `README.en.md` — снижает discoverability для международного сообщества.

### 21. **Нет publicado в PyPI**  
В `CONTRIBUTING.md` указано как нужная помощь, но не настроено.

### 22. **Отсутствует `CHANGELOG.md` / `RELEASES.md`**  
История изменений только в коммитах.

### 23. **Нет pre-commit hooks в репозитории**  
В `CONTRIBUTING.md` предлагается `pip install ruff` вручную, но нет `.pre-commit-config.yaml`.

### 24. **`conftest.py` использует `llm_provider="ollama"` без проверки доступности**  
Тесты могут падать на CI без Ollama. Лучше `provider="custom"` с mock или skip.

### 25. **Magic strings / hardcoded значения**  
- `"Архив"` хардкод в `vault.py`, `catalog.py` — не i18n
- `lease_seconds = 300` в `pipeline.py` — не конфигурируемо
- `SNIPPET_WINDOW = 240`, `CHUNK_TARGET_SIZE = 1500` в `search.py` — не конфигурируемо

### 26. **Отсутствует типизация возвращаемых значений в некоторых CLI-командах**  
Не все `Typer` колбэки имеют аннотации возврата.

### 27. **Логирование: нет структурированного JSON-лога для производственных сред**  
Только текстовый файл. Для контейнеров/системных сервисов нужен JSON.

---

## 🟢 Низкоприоритетные улучшения (P3 — Polish, nice-to-have)

### 28. **Добавить `py.typed` маркер** для лучшей поддержки type checkers пользователями библиотеки.

### 29. **Добавить `git commit --signoff` / DCO** в CONTRIBUTING.

### 30. **GitHub Discussions** включить для вопросов пользователей.

### 31. **Зависимости: зафиксировать версии в `uv.lock` и проверять `pip-audit` на CI** (уже есть, но проверить).

### 32. **Добавить badge "Tests passing" / "Coverage" в README** после настройки покрытия.

---

## 📊 Статистика кодовой базы

| Метрика | Значение |
|---------|----------|
| Python файлов (kb_agent/) | ~50 |
| Строк кода (приблизительно) | ~8,000 |
| Тестовых файлов | 38 |
| Тестов (pytest) | 297 |
| Покрытие тестами | Неизвестно (нет coverage в CI) |
| Зависимостей (prod) | 7 |
| Зависимостей (dev) | 5 |

---

## ✅ Что сделано хорошо (Strengths)

1. **Архитектура:** Чистое разделение слоев (CLI, Core, Processing, Sources, Storage, Agents), DI через конфиг
2. **Безопасность:** Pending-подтверждение write-операций, allowlist источников, redaction, local processing mode
3. **Идемпотентность:** `raw_queue` с `raw_hash`, `note_id` в очереди, обновление заметок по `external_id`
4. **Версионирование промптов:** `prompt_history` в SQLite, rollback, self-improve цикл
5. **Тесты:** 297 тестов, покрывают pipeline, relevance, search, MCP, security, migrations
6. **Миграции БД:** Версионированные шаги в `StateManager._SCHEMA` с `schema_version` таблицей
7. **MCP сервер:** Полная JSON-схемы инструментов, structuredContent, пагинация, annotations
8. **Документация:** Подробный README, SKILL.md для агентов, docs/ с архитектурой
9. **Конфигурация:** Pydantic v2, приоритет env > .env > defaults, валидация путей
10. **Локальность:** Полная работа с Ollama без внешних API, `KB_LOCAL_PROCESSING_MODE`

---

## 🎯 План исправлений (Roadmap)

### Этап 1: Критические блокеры (P0) — 2-3 часа
1. ✅ Исправить `untrusted_content` логику в `mcp_server.py`
2. ✅ Исправить `collect.yml`: эфемерные пути, сохранение/восстановление `state.db` между cron-запусками
3. ✅ Пробросить исключения в сборщиках (telegram.py, youtube.py) для корректного exit code
4. ✅ Исправить Telegram: брать последние посты `[-limit:]`, пропускать пустые
5. ✅ Исправить YouTube: `DEFAULT_SUB_LANGS.split(",")`, `raw_hash` после субтитров, `mark_processed_key` в Pipeline, `--playlist-end`
6. ✅ Добавить проверку `expires_at` в `PendingManager.confirm_change()`

### Этап 2: Логика ядра и конфигурация (P1) — 2-3 часа
7. ✅ Исправить приоритет загрузки `.env` в `config.py`
8. ✅ Удалить `_resource_path`, использовать `read_resource_text`
9. ✅ Исправить приоритет `get_prompt_text` (user dir → resources → config/)
10. ✅ Добавить парсинг inline-комментариев и `export ` в `_read_env_file`
11. ✅ Исправить заголовки: передавать `final_title` и в Vault, и в `index_note`
12. ✅ Исправить `catalog.py` / `vault.py`: Union[AppConfig, Path], None-safety, archived filter, atomic_write_text с utf-8
13. ✅ Почистить `pipeline.py`: дайджест, удалить мертвые методы
14. ✅ Добавить CI-проверку синхронизации `config/` и `kb_agent/resources/`

### Этап 3: Документация и CI (P2) — 1-2 часа
15. ✅ Починить Markdown в `README.md` (блок кода self-improve)
16. ✅ Дописать `SKILL.md`: YAML frontmatter, закончить обрезанную строку
17. ✅ Убрать `kb_confirm_change` и личные пути из `.env.example`, `.cursorrules`, `README.md`
18. ✅ Задокументировать все 12 MCP-инструментов
19. ✅ Добавить отсутствующие переменные в `.env.example`
20. ✅ Перенести `yt-dlp` в основные dependencies или обновить README
21. ✅ Убрать `requests` (заменить на `httpx`)
22. ✅ Добавить `uv lock --check` в `quality.yml`
23. ✅ Очистить корень репозитория: проектные черновики → `docs/history/` с kebab-case именами
24. ✅ Удалить дубли скриптов из `scripts/`
25. ✅ Настроить GitHub Topics из `pyproject.toml` keywords
26. ✅ Очистить историю от `.env.save` через `git filter-repo`

### Этап 4: Качество жизни (P3) — по мере возможностей
27. Английский README
28. PyPI publishing (GitHub Actions + trusted publishing)
29. CHANGELOG.md
30. Pre-commit hooks
31. JSON logging option
32. Конфигурируемые константы (lease_seconds, SNIPPET_WINDOW, etc.)

---

## 📝 Заключение

Проект **personal-kb-agent** — качественный, продуманный инструмент с современным стеком (Python 3.10+, Pydantic v2, MCP, SQLite FTS5). Архитектура масштабируема, безопасность учтена на уровне дизайна (pending-подтверждение, allowlist, redaction).

**Главные риски сейчас:** 
1. **Безопасность** — инвертированный `untrusted_content` (P0)
2. **Надежность CI** — неработающий автоматический сбор (P0)  
3. **Корректность данных** — сбор старых постов, потеря YouTube видео, несовпадение заголовков (P0/P1)

После исправления P0/P1 проблем проект готов к стабильному использованию и приему вкладов от сообщества.

---

*Отчет подготовлен автоматически на основе анализа кодовой базы, документации и существующего аудита (4audit_report_personal_kb_agent.md).*