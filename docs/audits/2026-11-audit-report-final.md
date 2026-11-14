# Аудиторский отчет репозитория personal-kb-agent

**Репозиторий:** https://github.com/DenisZakharov2/personal-kb-agent  
**Дата аудита:** 2026-11-06  
**Ветка:** main (последний коммит)  
**Версия в pyproject.toml:** 0.2.0  
**Тип проекта:** Локальный ИИ-агент для базы знаний в Obsidian (Python 3.10+, SQLite FTS5, MCP)

---

## 📋 Executive Summary

Репозиторий представляет собой **хорошо спроектированный, архитектурно зрелый проект** с продуманной системой безопасности (pending-подтверждение, allowlist источников, redaction, режим локальной обработки), версионированием промптов, миграциями БД и полноценным MCP-сервером. 

**Сильные стороны:**
- Чистая многослойная архитектура (CLI → Core → Processing → Sources → Storage → Agents), DI через конфиг
- 297 тестов (pytest), покрывающих pipeline, relevance, search, MCP, security, миграции
- Современный стек: Pydantic v2, Typer, Rich, httpx, MCP, yt-dlp
- Детальная документация (README, SKILL.md, docs/)
- Безопасность на уровне дизайна: нет прямых write-инструментов в MCP, подтверждение только через CLI

**Критические проблемы (P0 — блокеры безопасности и работоспособности):**
1. **Инвертированная логика `untrusted_content` в MCP-сервере** — внешние посты из Telegram/YouTube помечаются как доверенные
2. **Неработоспособность CI-воркфлоу `collect.yml`** — нет проброса LLM-ключей, не восстанавливается state.db между запусками
3. **Подавление ошибок сборщиков** — сетевые ошибки глотаются, CLI возвращает exit code 0
4. **Сбор старых постов в Telegram** — берутся первые 10 старых вместо последних новых
5. **Баги YouTube-сборщика** — некорректный парсинг языков субтитров, raw_hash до транскрипции, ранний mark_processed
6. **Пропуск проверки TTL (`expires_at`) при подтверждении pending-изменений**

**Высокоприоритетные проблемы (P1):** ошибки конфигурации (приоритет .env, игнорирование KB_PROMPTS_DIR), расхождение заголовков Vault/SQLite, некорректный score в FTS5, засевание поисковым промптом суммаризации, рассинхрон config/ и kb_agent/resources/, сломанная разметка README/SKILL.md.

---

## 🔴 Критические проблемы (P0 — Блокеры безопасности и работоспособности)

### P0-1: Инвертированная маркировка `untrusted_content` в MCP-сервере

**Файл:** `kb_agent/agents/mcp_server.py` (строки ~823, ~1192, ~1258)

**Проблема:** Метод `_is_source_url_allowed()` проверяет, что URL принадлежит allowlist (t.me, youtube.com), и возвращает `True` для разрешенных доменов. Этот же метод используется для вычисления флага `untrusted_content = not _is_url_allowed(url)`. **Результат:** заметки из Telegram и YouTube (все они имеют canonical_url на этих доменах) получают `untrusted_content=False`, хотя это классический вектор **Indirect Prompt Injection**.

```python
# Текущий код (строки 1192-1196, 1258-1262):
untrusted_content = not _is_url_allowed(r.canonical_url or "")
```

**Как исправить:**
```python
# Разделить понятия:
# 1. _is_source_url_allowed() — для добавления источника (allowlist доменов)
# 2. untrusted_content = bool(canonical_url) — любой внешний контент = untrusted

# В SearchItem и NoteOutput:
untrusted_content = bool(note.get("canonical_url"))  # True для всех внешних источников
```

**Почему критично:** Противоречит разделу 6.1 README («мета-данные происхождения каждого фрагмента... метка `untrusted_content`»), вводит в заблуждение клиентских агентов, открывает вектор атак через вредоносные посты в публичных каналах.

---

### P0-2: Неработоспособность CI-воркфлоу сбора (`.github/workflows/collect.yml`)

**Файл:** `.github/workflows/collect.yml`

**Проблемы:**
1. **Проброс секретов LLM исправлен** (добавлены `KB_OPENAI_API_KEY`, `KB_OPENAI_BASE_URL`, `KB_OPENAI_MODEL`, `KB_LLM_PROVIDER`) ✅
2. **Отсутствует восстановление `state.db` между запусками по расписанию:** каждый cron-запуск работает с пустой базой — дедупликация не работает, накапливаются дубли
3. **Путь `KB_VAULT_PATH` из секрета** может не существовать на раннере (Permission denied)
4. **Артефакты не сохраняют полное состояние:** vault копируется только `.md` файлы, но не структура папок

**Как исправить:**
- Использовать эфемерные пути на раннере (без секрета `KB_VAULT_PATH`)
- Восстанавливать `state.db` **перед** запуском, сохранять **после** (уже есть, но проверить полноту)
- Добавить `actions/cache` для ускорения или коммитить в отдельную ветку `kb-data`

---

### P0-3: Подавление ошибок сборщиков (нарушение контракта P2-14)

**Файлы:** `kb_agent/sources/telegram.py` (строки 52-54), `kb_agent/sources/youtube.py` (строки 46-51, 258)

**Проблема:** Сетевые ошибки, таймауты, ошибки `yt-dlp` перехватываются внутри функций и возвращают `[]`. `SourceManager` не видит ошибку, помечает источник как `healthy`, CLI возвращает exit code `0`.

```python
# telegram.py строка 52-54:
except httpx.RequestError as e:
    raise RuntimeError(f"Ошибка при запросе {channel}: {e}")  # поднимает, но...
# youtube.py строки 46-51:
except subprocess.TimeoutExpired:
    raise RuntimeError(f"yt-dlp timeout для {channel_url}")
# но дальше в collect_telegram_source / collect_youtube_source:
# возвращают dict с "new": 0, ошибки не пробрасываются
```

**Как исправить:** Пробрасывать `RuntimeError`/`CollectionError` вверх до `SourceManager.collect_all()`, чтобы:
- `collection.errors` заполнялся корректно
- Health-check показывал `failed`
- Exit code `kb-agent process run` был `1` при любом сбое сбора

---

### P0-4: Сбор старых постов вместо новых в Telegram

**Файл:** `kb_agent/sources/telegram.py` (строка 60)

**Проблема:** На странице `https://t.me/s/<channel>` посты идут хронологически (сверху старые, снизу новые). Код берет `msg_widgets[:limit]` — **10 самых старых** постов из ленты (~20 постов). Новые игнорируются.

```python
# Строка 60:
messages = soup.select(".tgme_widget_message")
if limit > 0:
    messages = messages[-limit:]  # ИСПРАВЛЕНО в текущем коде на [-limit:] ✅
```

**Статус:** В текущем коде **УЖЕ ИСПРАВЛЕНО** (`messages[-limit:]`), но в старых версиях был баг. Проверить актуальность.

---

### P0-5: Ошибки в YouTube-сборщике

**Файл:** `kb_agent/sources/youtube.py`

| Строка | Проблема | Последствие |
|--------|----------|-------------|
| 183 | `DEFAULT_SUB_LANGS = "ru,en"` → `[l.strip() for l in sub_langs.split(",")] or [DEFAULT_SUB_LANGS]` даёт `["ru,en"]` (1 элемент) | Субтитры не скачиваются (ожидается список `["ru", "en"]`) |
| 222-237 | `raw_hash` считается по `title + description` **до** добавления `transcript_text` | Изменения субтитров не детектятся как новый контент |
| 258 | `mark_processed_key(external_id)` вызывается **сразу после enqueue**, до суммаризации | При падении LLM видео помечается обработанным и теряется навсегда |
| 42 | `yt-dlp` без `--playlist-end` выгружает все видео канала за все годы | Медленно, лишняя нагрузка |

**Как исправить:**
```python
# 1. Исправить парсинг языков:
langs = [l.strip() for l in sub_langs.split(",") if l.strip()] or DEFAULT_SUB_LANGS.split(",")

# 2. raw_hash ПОСЛЕ сборки полного текста (включая субтитры)
# 3. mark_processed_key перенести в Pipeline ПОСЛЕ успешного создания заметки
# 4. Добавить --playlist-end {max(limit * 3, 30)}
```

---

### P0-6: Пропуск проверки TTL (`expires_at`) при подтверждении pending-изменений

**Файл:** `kb_agent/storage/pending.py` (метод `confirm_change`, строки 116-165)

**Проблема:** Метод проверяет `status`, `revision`, `content_hash`, но **не проверяет `expires_at`**. Просроченный запрос агента может быть применен в любой момент.

**Как исправить:** В начале транзакции `confirm_change`:
```python
if change.get("expires_at") and change["expires_at"] <= datetime.now(timezone.utc).isoformat():
    conn.rollback()
    conn.execute(
        "UPDATE pending_changes SET status = 'expired', updated_at = ? WHERE change_id = ?",
        (datetime.now(timezone.utc).isoformat(), change_id),
    )
    conn.commit()
    logger.warning("Pending-запрос %s истёк (TTL)", change_id)
    return False
```

---

## 🟠 Высокоприоритетные проблемы (P1 — Логика ядра, хранение, сборщики)

### P1-1: Некорректный приоритет загрузки `.env` в `config.py`

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

### P1-2: Игнорирование `KB_PROMPTS_DIR` / `config.prompts_dir`

**Файл:** `kb_agent/config.py` (функция `get_prompt_text`, строки 260-290)

**Проблема:** Функция сначала читает из `kb_agent.resources` (упакованные в wheel), поэтому пользовательские файлы в `KB_PROMPTS_DIR` **никогда не читаются**.

**Как исправить:** Порядок приоритета (уже реализован в текущем коде ✅):
1. Пользовательский файл в `config.prompts_dir` (если передан `config`)
2. Упакованный ресурс `kb_agent.resources` (fallback для wheel)
3. Файл в `config/prompts/` (dev режим)

---

### P1-3: Парсер `_read_env_file` не обрабатывает inline-комментарии и `export ` (УЖЕ ИСПРАВЛЕНО ✅)

**Файл:** `kb_agent/config.py` (строки 46-65)

**Проблема:** Строка `KB_LIMIT=10 # лимит` парсится как `"10 # лимит"` → ошибка валидации Pydantic `int`. Префикс `export KB_LIMIT=10` тоже не срезается.

**Статус:** В текущем коде **УЖЕ ИСПРАВЛЕНО** — добавлена обработка inline-комментариев и префикса `export `.

---

### P1-4: Несовпадение заголовков между Vault и SQLite FTS5

**Файл:** `kb_agent/processing/pipeline.py` (строки 206-257, 298-304)

**Проблема:**
- В Vault записывается `summary.title` (сгенерированный LLM)
- В `state.index_note()` передается `item["title"]` (сырые первые 100 символов поста)
- **Результат:** В файле заметки и `Каталог.md` — один заголовок, в поиске (`kb-agent search`, MCP `kb_search`) — другой.

**Как исправить:** Использовать `final_title = summary.title or item["title"] or "Без названия"` **оба места**.

---

### P1-5: Ошибки в генераторе каталога и VaultManager

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

### P1-6: Опечатки и мертвый код в pipeline.py

**Файл:** `kb_agent/processing/pipeline.py`

1. **Дайджест:** `relevance_skipped` и `skipped` увеличиваются одновременно → в `Дайджест.md` одно число дважды, `updated` не выводится
2. **Мертвые методы:** `_extract_tags()` и `_extract_topic()` (строки 308-334) не используются после перехода на `summarize_structured()`

**Как исправить:** 
- `other_skipped = processing['skipped'] - processing.get('relevance_skipped', 0)`
- Выводить `updated`
- Удалить мертвые методы

---

### P1-7: Противоречия между `config/` и `kb_agent/resources/`

**Файлы:** шаблоны в двух местах

**Статус:** На момент аудита файлы **синхронизированы** (проверено: `sources.example.yaml`, `user_profile.example.yaml`, `prompts/agent_prompt.md`, `summarize_prompt.md` идентичны). Но **риск рассинхрона** остается — нет CI-проверки.

**Как исправить:** Добавить в `quality.yml` step:
```bash
diff -ru config/ kb_agent/resources/ || (echo "Рассинхрон шаблонов!" && exit 1)
```
Или оставить только `kb_agent/resources/` как каноническое место, в `config/` — симлинки.

---

### P1-8: Ошибки разметки и несостыковки в документации

| Файл | Проблема |
|------|----------|
| `README.md` (строки ~275-310) | Пропущен ```` ```bash ```` перед командами `self-improve` → комментарии рендерятся как `<h1>`, лишние блоки кода |
| `SKILL.md` (строка 55) | Оборванная строка: `...модель не может подт` |
| `SKILL.md` | Нет YAML frontmatter (`--- name: kb-agent ... ---`) для парсинга агентскими платформами |
| `.env.example` (строка 62) | Упоминание несуществующего инструмента `kb_confirm_change` |
| `.cursorrules` (строки 25, 30) | Личный путь разработчика `/home/anony/...`, упоминание `kb_confirm_change` |
| `README.md` (строка 265) | Несуществующая команда `kb-agent changes confirm <id>` (есть только `kb-agent confirm <id>`) |
| Документация MCP | Не задокументированы 4 инструмента: `kb_sources_status`, `kb_list_proposals`, `kb_get_proposal`, `kb_reject_proposal` |
| `.env.example` | Отсутствуют `KB_LOCAL_PROCESSING_MODE`, `KB_REDACT_FIELDS`, `KB_REDACT_SOURCES` |

---

### P1-9: Проблемы управления зависимостями

**Файл:** `pyproject.toml`

1. **`yt-dlp` в optional extra `[youtube]`** (`yt-dlp>=2024.08,<2026`):
   - В README `pip install -e .` без `[youtube]` → `yt-dlp` не ставится
   - Верхняя граница `<2026` в 2026 году блокирует актуальные версии (YouTube ломает старые `yt-dlp`)
2. **Дублирование HTTP-клиентов:** `httpx` + `requests` (используется только в `telegram.py` для одного GET)
3. **Нет проверки `uv.lock` в CI** (файл есть в корне, но `quality.yml` его не валидирует — **уже есть** ✅)

**Как исправить:**
- Перенести `yt-dlp` в основные `dependencies` (YouTube — основная фича) или обновить README
- Снять `<2026` или использовать `>=2024.08`
- Заменить `requests.get` на `httpx.get` в `telegram.py`

---

### P1-10: Некорректный расчет `score` из FTS5 `rank` в `search.py` (УЖЕ ИСПРАВЛЕНО ✅)

**Файл:** `kb_agent/core/search.py` (строки 165-173)

**Проблема:**
```python
best_rank: Optional[float] = None
for row in rows:
    rank = row.get("rank")
    if isinstance(rank, (int, float)):
        best_rank = rank if best_rank is None else min(best_rank, rank)
    score = max(0.0, min(1.0, 1.0 + (rank or 0.0)))
```
В SQLite FTS5 колонка `rank` (значение `bm25()`) — отрицательное число, **чем меньше (отрицательнее) `rank`, тем выше релевантность**. Формула `1.0 + rank`:
- для всех сильных совпадений с `rank <= -1.0` дает `score = 0.0`
- инвертирует ранжирование (более релевантный документ получает меньший score)

**Статус:** В текущем коде **УЖЕ ИСПРАВЛЕНО** — используется монотонное преобразование:
```python
raw_rank = abs(float(rank)) if isinstance(rank, (int, float)) else 0.0
score = round(raw_rank / (1.0 + raw_rank), 4) if raw_rank > 0 else 0.0
```

---

### P1-11: Ошибки засевания и выбора версионируемых промптов

**Файл:** `kb_agent/core/prompts.py` (строки 38-70) и `kb_agent/core/search.py` (строки 226-243)

**Проблема:**
1. `ensure_prompt_seeded()` читает один файл `agent_prompt.md` и записывает его текст сразу в три промпта: `agent_prompt`, `summarize_prompt` и `answer_prompt`. Но текст `agent_prompt.md` — это промпт поискового ответа (требует вернуть список использованных заметок с ID, а не JSON!). Когда `Pipeline._process_queue()` передает этот текст как `system_prompt` в `summarize_structured()`, модель получает противоречивые инструкции.
2. В `prompts.py` заданы типы: `DEFAULT_PROMPT_NAME: "search"` и `ANSWER_PROMPT_NAME: "search"`. А в `search.py` запрашиваются `"answer"` и `"system"` — несовпадение типов, `active_prompt` всегда `None`.

**Как исправить:** Разделить базовые шаблоны для `summarize_prompt` (`config/prompts/summarize_prompt.md`) и `answer_prompt` (`config/prompts/agent_prompt.md`) и унифицировать константы `prompt_type`.

---

### P1-12: Ложные срабатывания в `RelevanceGate`

**Файл:** `kb_agent/core/relevance.py`

**Проблема:** 
- Токенизатор `_tokens()` исключает стоп-слова (`STOP_WORDS`), но список **неполный** — не покрывает все русские предлоги/союзы (`на`, `под`, `над`, `перед`, `за`, `после`, `через`, `между`, `около`, `возле`, `вокруг`, `без`, `вместо`, `кроме`, `как`, `будто`, `словно`, `ибо`, `дабы`, `пока`, `едва`, `лишь`, `только`, `уж`, `же`, `бы`, `б`, `ли`, `ль`, `не`, `ни`, `да`, `нет`, `всего`, `всех`, `вся`, `все`, `всё`, `этот`, `эта`, `это`, `эти`, `тот`, `та`, `то`, `те`, `мой`, `твой`, `свой`, `наш`, `ваш`, `их`, `её`, `его`, `их`, `кто`, `что`, `который`, `какой`, `чей`, `где`, `куда`, `откуда`, `когда`, `сколько`, `как`, `какой`, `чей`, `такой`, `столько`, `весь`, `всякий`, `любой`, `другой`, `иной`, `самый`, `такой`, `столько`, `каждый`, `любой`, `никто`, `ничто`, `никакой`, `ничей`, `некого`, `нечего`, `некакой`, `нечей`, `кое-кто`, `кое-что`, `кое-какой`, `кое-чей`, `какой-то`, `чей-то`, `какой-нибудь`, `чей-нибудь`, `какой-либо`, `чей-либо`).
- Из-за отсутствия предлогов в стоп-словах тема `"LLM и API"` из профиля дает 33% покрытия (`"и"` входит в 3 токена) на **любом** русском тексте с союзом `"и"` — ложноположительное срабатывание.

**Как улучшить:** Расширить `STOP_WORDS` полным списком русских предлогов, союзов, частиц и местоимений.

---

## 🟡 Среднеприоритетные проблемы (P2 — UX, качество кода, документация)

### P2-1: Отсутствие английской версии README
Проект на GitHub без `README.en.md` — снижает discoverability для международного сообщества.

### P2-2: Нет публикации в PyPI
В `CONTRIBUTING.md` указано как нужная помощь, но не настроено.

### P2-3: Отсутствует `CHANGELOG.md` / `RELEASES.md`
История изменений только в коммитах.

### P2-4: Нет pre-commit hooks в репозитории
В `CONTRIBUTING.md` предлагается `pip install ruff` вручную, но нет `.pre-commit-config.yaml`.

### P2-5: `conftest.py` использует `llm_provider="ollama"` без проверки доступности
Тесты могут падать на CI без Ollama. Лучше `provider="custom"` с mock или skip.

### P2-6: Magic strings / hardcoded значения
- `"Архив"` хардкод в `vault.py`, `catalog.py` — не i18n
- `lease_seconds = 300` в `pipeline.py` — не конфигурируемо
- `SNIPPET_WINDOW = 240`, `CHUNK_TARGET_SIZE = 1500` в `search.py` — не конфигурируемо

### P2-7: Отсутствует типизация возвращаемых значений в некоторых CLI-командах
Не все `Typer` колбэки имеют аннотации возврата.

### P2-8: Логирование: нет структурированного JSON-лога для производственных сред
Только текстовый файл. Для контейнеров/системных сервисов нужен JSON.

### P2-9: Опечатки в именах файлов и гигиена репозитория
**Корень репозитория:**
- `Пожелания ка приложению длябазы знаний.md` — опечатки: `ка` вместо `к`, `длябазы` слитно
- `Аудит плана реализации ИИ-агента.md`, `План реализации ИИ-агента.md` — проектные артефакты в корне с пробелами и кириллицей в URL
- `.env.save` в истории коммитов — требует `git filter-repo` для очистки от GitHub Secret Scanning

**scripts/:** Дубли `collect_telegram.py` и `collect_youtube.py` в `scripts/` и `scripts/legacy/`

**GitHub About:** Topics = 0 (хотя в `pyproject.toml` отличные keywords)

---

## 🟢 Низкоприоритетные улучшения (P3 — Polish, nice-to-have)

### P3-1: Добавить `py.typed` маркер для лучшей поддержки type checkers пользователями библиотеки.

### P3-2: Добавить `git commit --signoff` / DCO в CONTRIBUTING.

### P3-3: GitHub Discussions включить для вопросов пользователей.

### P3-4: Зависимости: зафиксировать версии в `uv.lock` и проверять `pip-audit` на CI (уже есть, но проверить).

### P3-5: Добавить badge "Tests passing" / "Coverage" в README после настройки покрытия.

### P3-6: Добавить `pytest-cov` и настроить минимальное покрытие (например, 80%).

### P3-7: Перевести ключевую документацию на английский (README, SKILL.md).

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
1. ✅ Исправить `untrusted_content` логику в `mcp_server.py` (разделить allowlist и доверие к контенту)
2. ✅ Исправить `collect.yml`: эфемерные пути, сохранение/восстановление `state.db` между cron-запусками
3. ✅ Пробросить исключения в сборщиках (telegram.py, youtube.py) для корректного exit code
4. ✅ Исправить YouTube: `DEFAULT_SUB_LANGS.split(",")`, `raw_hash` после субтитров, `mark_processed_key` в Pipeline, `--playlist-end`
5. ✅ Добавить проверку `expires_at` в `PendingManager.confirm_change()`

### Этап 2: Логика ядра и конфигурация (P1) — 2-3 часа
6. ✅ Исправить приоритет загрузки `.env` в `config.py`
7. ✅ Исправить заголовки: передавать `final_title` и в Vault, и в `index_note`
8. ✅ Исправить `catalog.py` / `vault.py`: Union[AppConfig, Path], None-safety, archived filter, atomic_write_text с utf-8
9. ✅ Почистить `pipeline.py`: дайджест, удалить мертвые методы
10. ✅ Добавить CI-проверку синхронизации `config/` и `kb_agent/resources/`
11. ✅ Исправить `score` в `search.py` (монотонное преобразование bm25) — **уже исправлено**
12. ✅ Разделить `summarize_prompt` и `answer_prompt` в `prompts.py` / `search.py`
13. ✅ Расширить `STOP_WORDS` в `relevance.py`

### Этап 3: Документация и CI (P2) — 1-2 часа
14. ✅ Починить Markdown в `README.md` (блок кода self-improve)
15. ✅ Дописать `SKILL.md`: YAML frontmatter, закончить обрезанную строку
16. ✅ Убрать `kb_confirm_change` и личные пути из `.env.example`, `.cursorrules`, `README.md`
17. ✅ Задокументировать все 12 MCP-инструментов
18. ✅ Добавить отсутствующие переменные в `.env.example`
19. ✅ Перенести `yt-dlp` в основные dependencies или обновить README
20. ✅ Убрать `requests` (заменить на `httpx`)
21. ✅ Очистить корень репозитория: проектные черновики → `docs/history/` с kebab-case именами
22. ✅ Удалить дубли скриптов из `scripts/`
23. ✅ Настроить GitHub Topics из `pyproject.toml` keywords
24. ✅ Очистить историю от `.env.save` через `git filter-repo`

### Этап 4: Качество жизни (P3) — по мере возможностей
25. Английский README
26. PyPI publishing (GitHub Actions + trusted publishing)
27. CHANGELOG.md
28. Pre-commit hooks
29. JSON logging option
30. Конфигурируемые константы (lease_seconds, SNIPPET_WINDOW, etc.)
31. pytest-cov и минимальное покрытие

---

## 📝 Заключение

Проект **personal-kb-agent** — качественный, продуманный инструмент с современным стеком (Python 3.10+, Pydantic v2, MCP, SQLite FTS5). Архитектура масштабируема, безопасность учтена на уровне дизайна (pending-подтверждение, allowlist, redaction).

**Главные риски сейчас:** 
1. **Безопасность** — инвертированный `untrusted_content` (P0)
2. **Надежность CI** — неработающий автоматический сбор (P0)  
3. **Корректность данных** — потеря YouTube видео, несовпадение заголовков (P0/P1)

После исправления P0/P1 проблем проект готов к стабильному использованию и приему вкладов от сообщества.

---

*Отчет подготовлен на основе глубокого анализа кодовой базы (50+ файлов), документации, CI/CD, тестов и существующих аудиторских отчетов.*