# Комплексный аудиторский отчет по репозиторию `DenisZakharov2/personal-kb-agent`

**Объект аудита:** [DenisZakharov2/personal-kb-agent](https://github.com/DenisZakharov2/personal-kb-agent) (ветка `main`, актуальное состояние после 58 коммитов, включая закрытие волны `R-01...R-32` и коммит `cafb183`)  
**Дата проведения аудита:** 5 ноября 2026 г.  
**Охват проверки:** 100% файлов репозитория — метаданные GitHub, корневые документы, шаблоны конфигураций (`config/` и `kb_agent/resources/`), CI/CD пайплайны (`.github/workflows/`), исходный код пакета (`kb_agent/`), скрипты (`scripts/`) и тестовый набор (`tests/`).

## 1. Резюме аудита (Executive Summary)

Репозиторий [DenisZakharov2/personal-kb-agent](https://github.com/DenisZakharov2/personal-kb-agent) представляет собой продуманную по замыслу систему управления личной базой знаний («Второй мозг») на стеке Python 3.10+, SQLite (FTS5), Pydantic v2, Typer и MCP (Model Context Protocol) с интеграцией в Obsidian. В ходе недавней серии коммитов в проекте была проделана большая работа по закрытию замечаний предыдущего аудита (добавлены тесты в `tests/`, внедрены lease-блокировки в очереди, структурированный JSON-вывод LLM, разделение `config/` и `kb_agent/resources/`, защита от записи вне allowlist).

Однако глубокая перекрестная проверка актуального кода, конфигураций, тестов и документации выявила **34 актуальные проблемы**, среди которых **8 критических и высокоприоритетных дефектов времени выполнения (P0/P1)**, появившихся в том числе как регрессии при последнем рефакторинге:

1. **Блокирующая ошибка валидации URL Telegram (`P0`)**: Регулярное выражение `_TELEGRAM_URL_RE` в [`kb_agent/sources/registry.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/sources/registry.py) запрещает сегмент `/s/` в URL вида `https://t.me/s/<канал>`, хотя именно этот формат требуется в тексте ошибки, [`README.md`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/README.md), [docs/workflow.md](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/blob/main/docs/workflow.md) и [config/sources.example.yaml](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/blob/main/config/sources.example.yaml).
2. **Разорванная инициализация источников между YAML и SQLite (`P0`)**: В [`kb_agent/processing/pipeline.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/processing/pipeline.py) функция `ensure_sources_file()` создает `data/sources.yaml` из переменных `.env`, но [`kb_agent/sources/manager.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/sources/manager.py) читает источники только из таблицы `sources` в SQLite без вызова `import_from_yaml()`. На чистой установке и в CI конвейер собирает 0 материалов.
3. **Падение MCP-инструмента `kb_get_insights` (`P0`)**: В [`kb_agent/core/insights.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/core/insights.py) поле `confidence` формируется как строка (`"high"` / `"medium"`), а в [`kb_agent/agents/mcp_server.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/agents/mcp_server.py) схема `InsightOutput` требует `float`, что вызывает `ValidationError` при наличии хотя бы одного инсайта в БД.
4. **Утечка приватных заметок, сломанная пагинация и холостой вызов LLM в MCP `kb_search` (`P0`)**: В [`kb_agent/agents/mcp_server.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/agents/mcp_server.py) инструмент `kb_search` не фильтрует заметки с `is_private=1` (вопреки разделу 6.2 `README.md`), обрезает выборку до `limit` до применения смещения `cursor` (делая вторую страницу всегда пустой) и при каждом поисковом запросе вызывает генерацию ответа LLM в `SearchEngine.search()`, полностью отбрасывая сгенерированный ответ.
5. **Нерабочий CI-воркфлоу сбора [`.github/workflows/collect.yml`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/.github/workflows/collect.yml) (`P0`)**: В окружение раннера не передается `KB_OPENAI_API_KEY` (и другие параметры LLM), а база `state.db` создается в `${{ runner.temp }}` с нуля при каждом запуске без восстановления предыдущего состояния.
6. **Ложноположительный и ложноотрицательный фильтр `RelevanceGate` (`P1`)**: В [`kb_agent/core/relevance.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/core/relevance.py) не удаляются русские союзы и предлоги, из-за чего тема `"LLM и API"` из [config/user\_profile.example.yaml](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/blob/main/config/user_profile.example.yaml) дает 33.3% совпадения (`>= 0.3`) на любом тексте с союзом `"и"`, а проверка спама по подстрокам (`"купи"`, `"вложения"`) блокирует статьи со словами `"купили"`, `"окупился"`, `"вложения в инфраструктуру"`.
7. **Рассинхронизация дублирующихся ресурсов `config/` и `kb_agent/resources/` (`P1`)**: Упакованные в пакет файлы в [kb\_agent/resources/](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/tree/main/kb_agent/resources) содержат старые тексты, немецкое слово `weiteren`, грамматические ошибки и прямо противоположное утверждение об источнике истины (`только YAML, не дублирование в SQLite`).
8. **Сломанная разметка в [`README.md`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/README.md), обрыв текста в [SKILL.md](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/blob/main/SKILL.md) и опечатки в названиях файлов (`P2`)**: В `README.md` пропущен открывающий тег блока кода ```` ```bash ```` перед командами `self-improve`, из-за чего комментарии рендерятся как заголовки H1, а абзацы текста попали внутрь блока кода; в корне лежит файл с двойной опечаткой в имени [`Пожелания ка приложению длябазы знаний.md`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/%D0%9F%D0%BE%D0%B6%D0%B5%D0%BB%D0%B0%D0%BD%D0%B8%D1%8F%20%D0%BA%D0%B0%20%D0%BF%D1%80%D0%B8%D0%BB%D0%BE%D0%B6%D0%B5%D0%BD%D0%B8%D1%8E%20%D0%B4%D0%BB%D1%8F%D0%B1%D0%B0%D0%B7%D1%8B%20%D0%B7%D0%BD%D0%B0%D0%BD%D0%B8%D0%B9.md).

## 2. Сводная матрица выявленных проблем

| ID | Приоритет | Категория | Затронутые файлы | Краткое описание проблемы |
| :--- | :--- | :--- | :--- | :--- |
| **A-01** | **P0 (Critical)** | Баг валидации | [`kb_agent/sources/registry.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/sources/registry.py) | `_TELEGRAM_URL_RE` отклоняет URL с `/s/` (`https://t.me/s/chan`), которые сам же требует в сообщении об ошибке и документации |
| **A-02** | **P0 (Critical)** | Архитектура / Сбор | [`kb_agent/processing/pipeline.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/processing/pipeline.py), [`kb_agent/sources/manager.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/sources/manager.py) | `ensure_sources_file` пишет в `data/sources.yaml`, а `SourceManager` читает только таблицу `sources` в SQLite без импорта YAML |
| **A-03** | **P0 (Critical)** | Баг MCP | [`kb_agent/agents/mcp_server.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/agents/mcp_server.py), [`kb_agent/core/insights.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/core/insights.py) | `kb_get_insights` падает с `ValidationError`: `Insight.confidence` — строка (`"high"`), а `InsightOutput.confidence` — `float` |
| **A-04** | **P0 (Critical)** | Приватность / MCP | [`kb_agent/agents/mcp_server.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/agents/mcp_server.py) | `kb_search` выдает внешнему агенту приватные заметки (`is_private=1`), имеет сломанную пагинацию `cursor` и вхолостую вызывает LLM |
| **A-05** | **P0 (Critical)** | Безопасность MCP | [`kb_agent/agents/mcp_server.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/agents/mcp_server.py) | Инвертированная логика `untrusted_content`: все внешние посты из `t.me` и `youtube.com` помечаются как `untrusted_content=False` |
| **A-06** | **P0 (Critical)** | CI/CD | [`.github/workflows/collect.yml`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/.github/workflows/collect.yml) | Не передается `KB_OPENAI_API_KEY`, не восстанавливается `state.db` между запусками, пустой `sources` в SQLite |
| **A-07** | **P1 (High)** | Логика фильтрации | [`kb_agent/core/relevance.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/core/relevance.py) | Любой русский текст с союзом `"и"` проходит тему `"LLM и API"` (`1/3 = 33% >= 30%`); подстрочный спам-фильтр режет нормальные слова; латинская `a` в `[a-яё...]` |
| **A-08** | **P1 (High)** | Логика поиска | [`kb_agent/core/search.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/core/search.py) | Формула `1.0 + rank` обнуляет `score` для лучших совпадений FTS5 (`rank <= -1.0`) и инвертирует ранжирование |
| **A-09** | **P1 (High)** | Промпты и БД | [`kb_agent/core/prompts.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/core/prompts.py), [`kb_agent/core/search.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/core/search.py) | Поисковый `agent_prompt.md` засевается в `summarize_prompt`; конфликт `prompt_type` (`"search"` vs `"answer"` / `"system"`) |
| **A-10** | **P1 (High)** | Конфигурация | [`kb_agent/config.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/config.py) | `default_env` перезаписывает явно переданный `env_file`; баг `with importlib.resources.files()` в `_resource_path`; игнорируется `prompts_dir` |
| **A-11** | **P1 (High)** | Хранилище и Каталог | [`kb_agent/storage/catalog.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/storage/catalog.py), [`kb_agent/storage/vault.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/storage/vault.py) | `rebuild_catalog` падает с `AttributeError`; `generate()` падает при `null` в YAML и включает папку `Архив/`; запись без `encoding="utf-8"` |
| **A-12** | **P1 (High)** | Пайплайн и индекс | [`kb_agent/processing/pipeline.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/processing/pipeline.py) | В SQLite FTS5 индексируется сырой `item["title"]` вместо `summary.title`; в `Дайджест.md` задвоено число пропусков |
| **A-13** | **P1 (High)** | Сборщики источников | [`kb_agent/sources/telegram.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/sources/telegram.py), [`kb_agent/sources/youtube.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/sources/youtube.py) | Сетевые ошибки глотаются с возвратом `[]` (ложный `status="ok"` и код возврата `0`); `[:limit]` берет старые посты; баги `raw_hash` и `DEFAULT_SUB_LANGS` |
| **A-14** | **P1 (High)** | Очередь `pending` | [`kb_agent/storage/pending.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/storage/pending.py) | `confirm_change` не проверяет истечение `expires_at` (TTL); `_update_profile` игнорирует текущую конфигурацию |
| **A-15** | **P1 (High)** | Рассинхрон пакета | [config/](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/tree/main/config) vs [kb\_agent/resources/](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/tree/main/kb_agent/resources) | Противоречащие шаблоны и устаревший промпт с опечатками внутри `kb_agent/resources/` |
| **A-16** | **P2 (Medium)** | Документация | [`README.md`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/README.md), [SKILL.md](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/blob/main/SKILL.md), [.cursorrules](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/blob/main/.cursorrules), [.env.example](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/blob/main/.env.example) | Сломанный блок кода в `README.md`, обрыв строки в `SKILL.md`, ссылки на удаленный `kb_confirm_change` и личный путь `/home/anony/...` |
| **A-17** | **P2 (Medium)** | Зависимости и CI | [`pyproject.toml`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/pyproject.toml), [`.github/workflows/quality.yml`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/.github/workflows/quality.yml) | Потолок `yt-dlp<2026`, дублирование `httpx` и `requests`, исключение `tests/` из `ruff`, неиспользование `uv.lock` и `mypy` |
| **A-18** | **P3 (Low)** | Гигиена репозитория | Корень репозитория, [docs/](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/tree/main/docs), [scripts/](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/tree/main/scripts) | Опечатки в именах файлов (`Пожелания ка приложению длябазы знаний.md`), дубли скриптов в `scripts/` и `scripts/legacy/`, отсутствие Topics на GitHub |

## 3. Детальный разбор ошибок в коде и архитектуре (P0 / P1)

### A-01 (P0): Невозможность добавить Telegram-источник по каноническому URL `https://t.me/s/<канал>`

- **Локация:** [`kb_agent/sources/registry.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/sources/registry.py) (строки 29–53)
- **Суть дефекта:** В модуле объявлено регулярное выражение валидации URL Telegram-канала:
  ```python
  _TELEGRAM_URL_RE = re.compile(r"^https?://(?:www\.)?t\.me/[A-Za-z0-9_]+/?$")
  ```
  При этом в функции `normalize_source` (строки 50–53) написано:
  ```python
  if source_type == "telegram":
      if not _TELEGRAM_URL_RE.match(url):
          raise ValueError(
              f"Неправильный URL Telegram-канала: {url}. Ожидается https://t.me/s/<канал>."
          )
  ```
  Регулярное выражение `^https?://(?:www\.)?t\.me/[A-Za-z0-9_]+/?$` допускает только один сегмент пути после домена (`https://t.me/ragnarokzone`), но **отклоняет** любой URL с префиксом публичного веб-превью `/s/` (`https://t.me/s/ragnarokzone`). При этом во всех примерах в [`README.md`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/README.md) (строка 132), [docs/workflow.md](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/blob/main/docs/workflow.md) (строка 35), [config/sources.example.yaml](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/blob/main/config/sources.example.yaml) (строка 12) и в самой функции `_sources_from_config` (строка 303 `registry.py`) используется именно `https://t.me/s/<канал>`.
- **Как улучшить:**
  ```python
  _TELEGRAM_URL_RE = re.compile(
      r"^(?:https?://(?:www\.)?(?:t\.me|telegram\.me)/(?:s/)?)?@?([A-Za-z0-9_]{4,64})/?$"
  )

  def normalize_source(
      source_type: str, url: str, source_id: Optional[str] = None
  ) -> Tuple[str, str, str]:
      if source_type not in SOURCE_TYPES:
          raise ValueError(
              f"Неподдерживаемый тип источника: {source_type}. Доступны: {', '.join(SOURCE_TYPES)}."
          )
      url = url.strip()
      if source_type == "telegram":
          m = _TELEGRAM_URL_RE.match(url)
          if not m:
              raise ValueError(
                  f"Неправильный URL Telegram-канала: {url}. "
                  "Ожидается https://t.me/s/<канал>, https://t.me/<канал> или @<канал>."
              )
          channel_slug = m.group(1)
          canonical_url = f"https://t.me/s/{channel_slug}"
          resolved_id = source_id or f"tg_{channel_slug}"
          return resolved_id, canonical_url, source_type
  ```
- **Почему нужно сделать именно так:** Пользователи и внешние ИИ-агенты (через MCP `kb_add_source`, где в описании параметра `channel` в [`kb_agent/agents/mcp_server.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/agents/mcp_server.py) на строке 1048 явно написано: `Идентификатор канала (например, rubiconzone или @amorev)`) передают как короткие имена (`rubiconzone`, `@rubiconzone`), так и ссылки `https://t.me/rubiconzone` или `https://t.me/s/rubiconzone`. Приведение любого из этих форматов к единому каноническому URL `https://t.me/s/<slug>` устраняет расхождение между CLI, MCP, `.env` и `sources.example.yaml`.

### A-02 (P0): Игнорирование `data/sources.yaml` и переменных `.env` при первом запуске конвейера

- **Локация:** [`kb_agent/processing/pipeline.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/processing/pipeline.py) (строки 65–72), [`kb_agent/sources/manager.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/sources/manager.py) (строки 40–46), [`kb_agent/sources/registry.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/sources/registry.py) (строки 86–105)
- **Суть дефекта:** В `Pipeline.run()` вызывается `ensure_sources_file(config=self.config)`, который генерирует файл `data/sources.yaml` из переменных `KB_TELEGRAM_CHANNELS` и `KB_YOUTUBE_CHANNELS` (или копирует `sources.example.yaml`). Однако сразу после этого `run_collection()` создает `SourceManager(config, state)`, который инициализирует `SourceRegistry(sources_path=registry_path, state=state)`. Поскольку `state` передан (`self._use_sqlite = True`), метод `SourceRegistry.load()` выполняет `SELECT ... FROM sources` из SQLite и **полностью игнорирует** созданный `data/sources.yaml`.
  Более того, если после этого вызвать любую операцию сохранения реестра, `SourceRegistry.save()` вызовет `_export_to_yaml()` и перезапишет `data/sources.yaml` пустым списком из SQLite.
- **Как улучшить:** В `SourceRegistry._load_from_sqlite()` (или при инициализации в `Pipeline.run()`) выполнять автоматический начальный посев (seed) таблицы `sources` из `sources.yaml` (и переменных окружения `KB_TELEGRAM_CHANNELS` / `KB_YOUTUBE_CHANNELS`), если таблица `sources` в SQLite пуста:
  ```python
  def _load_from_sqlite(self) -> None:
      if self.state is None:
          self._sources = {}
          return
      conn = self.state.connect()
      rows = conn.execute(
          "SELECT source_id, source_type, channel, enabled, updated_at FROM sources ORDER BY source_id"
      ).fetchall()
      if not rows and self.sources_path.exists():
          self._loaded = True
          self.import_from_yaml(self.sources_path)
          return
      self._sources = {row["source_id"]: self._row_to_config(dict(row)) for row in rows}
  ```
- **Почему нужно сделать именно так:** Это связывает задокументированный в [`README.md`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/README.md) и [.env.example](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/blob/main/.env.example) сценарий быстрого старта (настройка `KB_TELEGRAM_CHANNELS` / `KB_YOUTUBE_CHANNELS` в `.env` -> запуск `kb-agent process run`) с каноническим SQLite-хранилищем, сохраняя таблицу `sources` в `state.db` единым источником истины после первого запуска.

### A-03 (P0): Гарантированное падение MCP-инструмента `kb_get_insights` на непустой базе

- **Локация:** [`kb_agent/core/insights.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/core/insights.py) (строки 28, 97, 128, 154) и [`kb_agent/agents/mcp_server.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/agents/mcp_server.py) (строки 911–916, 999–1004, 1276–1298)
- **Суть дефекта:**
  1. В `kb_agent/core/insights.py` класс `Insight` определяет `confidence: str` со значениями `"high"`, `"medium"`, `"low"`.
  2. В `kb_agent/agents/mcp_server.py` модель `InsightOutput` определяет `confidence: float`:
     ```python
     class InsightOutput(BaseModel):
         statement: str
         link_type: str
         confidence: float
         evidence_note_ids: list[str]
         benefit: str
     ```
     При вызове `kb_get_insights`, как только `InsightsEngine` находит хотя бы один повторяющийся тег, пробел по теме профиля или связь в `note_links`, вызов `InsightOutput(..., confidence=i.confidence)` падает с `pydantic.ValidationError` (`Input should be a valid number, unable to parse string as a number [input_value='high']`).
  3. В `_LIST_TOOLS` (строка 1003) в качестве `outputSchema` инструмента `kb_get_insights` передана схема одиночного объекта `InsightOutput.model_json_schema()`, тогда как обработчик (строка 1289) возвращает словарь `{"insights": [...]}`.
- **Как улучшить:** Изменить тип `confidence` в `InsightOutput` на `str` (или `Literal["high", "medium", "low"]`) и создать обертку `InsightsListOutput(BaseModel)` с полем `insights: list[InsightOutput]` для `outputSchema`.
- **Почему нужно сделать именно так:** MCP-клиенты, валидирующие `structuredContent` по `outputSchema` (включая актуальный SDK `mcp`), отклоняют ответы, структура которых не совпадает с заявленной JSON Schema, а несовпадение типов `str` и `float` делает инструмент `kb_get_insights` полностью неработоспособным на реальных данных.

### A-04 (P0): Утечка приватных заметок, нерабочая пагинация и лишний вызов LLM в MCP `kb_search`

- **Локация:** [`kb_agent/agents/mcp_server.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/agents/mcp_server.py) (строки 1152–1232) и [`kb_agent/core/search.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/core/search.py) (строки 148–275)
- **Суть дефекта:**
  1. **Утечка приватных данных:** В разделе 6.2 [`README.md`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/README.md) заявлено: *«Приватные заметки не возвращаются внешнему агенту через `kb_get_note` и фильтруются из результатов `kb_search` для внешнего MCP-клиента»*. В `kb_get_note` (строка 1240) проверка `if bool(note.get("is_private", 0)): return _error_result("forbidden", ...)` есть, но в `kb_search` (строки 1160–1201) фильтрации по `r.is_private` **нет** — приватные заметки возвращаются внешнему агенту вместе со сниппетом и `content_preview` (500 символов).
  2. **Сломанная пагинация (`cursor`):**
     ```python
     response = engine.search(query, limit=limit)
     items: list[SearchItem] = []
     for r in response.results[cursor : cursor + limit]:
     ```
     `engine.search(query, limit=limit)` возвращает не более `limit` записей. При запросе следующей страницы (`limit=5, cursor=5`) срез `response.results[5:10]` берется от списка из 5 элементов и **всегда возвращает `[]`**, а на первой странице (`cursor=0, limit=5`) условие `cursor + len(items) < len(response.results)` (`5 < 5`) всегда ложно, поэтому `next_cursor` всегда `None`.
  3. **Холостой вызов LLM в `kb_search`:** `mcp_server.py` вызывает `engine.search(query, limit=limit)`, который внутри себя обязательно обращается к внешнему LLM (`self.llm.generate(...)`) для генерации `response.answer`. При этом `kb_search` в `mcp_server.py` поле `response.answer` вообще не использует!
  4. **Дублирование кода и потеря `content_hash`:** В строках 1164–1177 `mcp_server.py` список `prov_parts` собирается в цикле и нигде не используется, затем повторно собирается в строках 1208–1221, причем в конструктор `SearchItem` забыли передать `content_hash=r.content_hash`, из-за чего `it.content_hash` всегда `None`.
- **Как улучшить:**
  1. Разделить в `SearchEngine` чистый поиск по FTS5 (`search_notes(query, limit, offset, include_private=False)`) и генерацию ответа LLM (`ask(query, ...)`), либо добавить в `SearchEngine.search()` параметры `generate_answer: bool = True`, `offset: int = 0`, `include_private: bool = True`.
  2. В `mcp_server.py` вызывать поиск с `generate_answer=False`, `include_private=False` и передавать `offset=cursor, limit=limit + 1` в SQL (`LIMIT ? OFFSET ?`), чтобы достоверно определять наличие следующей страницы (`next_cursor`).
- **Почему нужно сделать именно так:** Инструмент `kb_search` вызывается внешним ИИ-агентом, который сам является LLM и нуждается в быстрых (единицы миллисекунд) результатах FTS5 без каскадного ожидания второго LLM-запроса и расхода токенов, а фильтрация `is_private=0` на уровне SQL-запроса гарантирует соблюдение политики конфиденциальности и корректную работу пагинации.

### A-05 (P0): Инвертированная маркировка недоверенного контента (`untrusted_content`) в MCP

- **Локация:** [`kb_agent/agents/mcp_server.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/agents/mcp_server.py) (строки 823–846, 1192–1196, 1258–1262)
- **Суть дефекта:** В `mcp_server.py` список `ALLOWED_HOSTS` содержит `t.me`, `youtube.com`, `youtu.be` (домены, разрешенные для добавления в качестве источников). Однако этот же метод `_is_url_allowed()` используется для вычисления флага безопасности `untrusted_content`:
  ```python
  untrusted_content=not _is_url_allowed(r.canonical_url or "")
  if r.canonical_url
  else True
  ```
  Так как все заметки из Telegram и YouTube имеют `canonical_url` на доменах `t.me` и `youtube.com`, `_is_url_allowed()` возвращает для них `True`, а `not _is_url_allowed(...)` выставляет `untrusted_content = False`!
- **Как улучшить:** Разделить понятия «разрешенный домен источника для парсинга» (`_is_source_url_allowed`) и «доверие к содержимому заметки» (`untrusted_content = True` для любого внешнего контента, собранного из публичных Telegram-каналов и YouTube, в отличие от собственных локальных заметок пользователя).
- **Почему нужно сделать именно так:** Посты из публичных Telegram-каналов и субтитры YouTube — это классический вектор косвенных промпт-инъекций (Indirect Prompt Injection). Помечать их флагом `untrusted_content=False` прямо противоречит разделу 6.1 [`README.md`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/README.md) и вводит в заблуждение клиентского агента.

### A-06 (P0): Неработоспособность CI-воркфлоу [`.github/workflows/collect.yml`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/.github/workflows/collect.yml)

- **Локация:** [`.github/workflows/collect.yml`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/.github/workflows/collect.yml) (строки 29–58)
- **Суть дефекта:**
  1. В шаге `Configure environment` в `$GITHUB_ENV` пробрасываются только `KB_TELEGRAM_CHANNELS` и `KB_YOUTUBE_CHANNELS`, но **не пробрасываются** `KB_OPENAI_API_KEY`, `KB_OPENAI_BASE_URL`, `KB_OPENAI_MODEL` и `KB_LLM_PROVIDER`. Без API-ключа шаг `kb-agent process run` не может суммаризировать ни один материал.
  2. Из-за дефекта **A-02** переменные `KB_TELEGRAM_CHANNELS` и `KB_YOUTUBE_CHANNELS` записываются только в `sources.yaml`, но не попадают в таблицу `sources` в `state.db`, поэтому раннер собирает 0 постов.
  3. База `state.db` и папка `vault` создаются во временной директории `${{ runner.temp }}` и выгружаются в артефакт `kb-state`, но перед запуском предыдущий артефакт не скачивается — каждый запуск по расписанию работает с пустой базой без истории дедупликации.
  4. Если в `secrets.KB_VAULT_PATH` задан локальный путь разработчика (например, `/home/denis/...`), шаг `mkdir -p "${{ env.KB_VAULT_PATH }}"` на раннере GitHub Actions падает с `Permission denied`.
- **Как улучшить:** Пробросить секреты LLM (`KB_OPENAI_API_KEY`, `KB_OPENAI_BASE_URL`, `KB_OPENAI_MODEL`, `KB_LLM_PROVIDER`), убрать переопределение `KB_VAULT_PATH` из секрета на эфемерном раннере и добавить восстановление/сохранение состояния (либо через коммит в выделенную ветку данных `kb-data`, либо через загрузку предыдущего артефакта/кэша).
- **Почему нужно сделать именно так:** Без передачи ключей LLM и сохранения `state.db` между запусками автоматический сбор по расписанию (`cron: '0 10 * * *'`) не выполняет свою функцию.

### A-07 (P1): Ложные срабатывания и пропуски в `RelevanceGate` ([`kb_agent/core/relevance.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/core/relevance.py))

- **Локация:** [`kb_agent/core/relevance.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/core/relevance.py) (строки 20, 62–150)
- **Суть дефекта:**
  1. **Пропуск любого текста из-за союзов/предлогов в темах:** Токенизатор `_tokens()` не фильтрует русские и английские стоп-слова (`"и"`, `"в"`, `"на"`, `"для"`, `"с"`, `"по"`, `"о"`, `"and"`, `"of"`). В шаблоне [config/user\_profile.example.yaml](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/blob/main/config/user_profile.example.yaml) задана тема `"LLM и API"`, которая разбивается на 3 токена: `{"llm", "и", "api"}`. Любой русскоязычный пост на кулинарную или бытовую тему, содержащий союз `"и"`, дает совпадение 1 из 3 токенов: `1 / 3 = 0.3333 >= 0.3` (`DEFAULT_THRESHOLD = 0.3`), и `RelevanceGate` пропускает его как релевантный (`соответствует профилю (33%)`)!
  2. **Блокировка полезных постов подстрочным спам-фильтром:** Проверка `for kw in spam_keywords: if kw in text:` ищет подстроки без границ слов (``). Слова `"купи"`, `"вложения"`, `"скидка"`, `"реклама"` являются частями обычных слов: любой пост со словами `"купили"`, `"закупили"`, `"окупился"`, `"вложения в ИИ"`, `"вложения электронной почты"` помечается как спам и отбрасывается.
  3. **Опечатка в кодировке регулярного выражения `_WORD_RE`:** В строке 20 записано `_WORD_RE = re.compile(r"[a-яёa-z0-9]+", re.IGNORECASE)`, где первая буква `a` — латинская (`U+0061`), а `я` — кириллическая (`U+044F`). Диапазон `U+0061..U+044F` в регулярном выражении захватывает все символы ASCII от `a` до `~` (включая `{`, `|`, `}`, `~`) и сотни небуквенных символов Unicode.
- **Как улучшить:**
  1. Исправить регулярное выражение на `re.compile(r"[а-яёa-z0-9]{2,}", re.IGNORECASE)` (с кириллической `а` и минимальной длиной токена 2 символа) и исключить список стоп-слов (`STOP_WORDS = {"и", "в", "во", "на", "с", "со", "к", "ко", "по", "из", "за", "от", "до", "для", "про", "или", "но", "не", "ни", "что", "как", "это", "все", "and", "or", "the", "in", "on", "to", "for", "of", "with"}`).
  2. Для спам-фраз использовать компилированное регулярное выражение по границам слов (`r"(?:розыгрыш\w*|giveaway|промокод\w*|бесплатный розыгрыш|подпишись на канал)"`), убрав из безусловного бана общеупотребительные корни `"вложения"`, `"реклама"`, `"купи"`.
- **Почему нужно сделать именно так:** Исключение предлогов и союзов из расчета покрытия темы предотвращает 100%-ное прохождение нерелевантного русскоязычного текста через порог `0.3`, а проверка спама по границам слов защищает технические и аналитические статьи от ложного удаления.

### A-08 (P1): Некорректный расчет `score` из FTS5 `rank` в [`kb_agent/core/search.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/core/search.py)

- **Локация:** [`kb_agent/core/search.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/core/search.py) (строки 165–173)
- **Суть дефекта:**
  ```python
  best_rank: Optional[float] = None
  for row in rows:
      rank = row.get("rank")
      if isinstance(rank, (int, float)):
          best_rank = rank if best_rank is None else min(best_rank, rank)
      score = max(0.0, min(1.0, 1.0 + (rank or 0.0)))
  ```
  В SQLite FTS5 колонка `rank` (значение `bm25()`) — это отрицательное число без ограничения сверху по модулю (например, `-12.4`, `-3.8`, `-0.4`), причем **чем меньше (отрицательнее) `rank`, тем выше релевантность документа**. Формула `1.0 + rank`:
  - для всех сильных совпадений с `rank <= -1.0` дает `1.0 + rank <= 0.0`, превращая `score` в `0.00`;
  - для значений `-1.0 < rank < 0.0` дает более релевантному документу (`rank = -0.8 -> score = 0.20`) меньший `score`, чем слабому совпадению (`rank = -0.1 -> score = 0.90`)!
  Переменная `best_rank` при этом вычисляется, но нигде не используется.
- **Как улучшить:** Использовать монотонное преобразование `bm25` в диапазон `(0.0, 1.0]` или нормализацию относительно `best_rank`:
  ```python
  raw_rank = abs(float(rank)) if isinstance(rank, (int, float)) else 0.0
  score = round(raw_rank / (1.0 + raw_rank), 4) if raw_rank > 0 else 0.0
  ```
- **Почему нужно сделать именно так:** Функция `abs(rank) / (1 + abs(rank))` строго возрастает с ростом релевантности (`|bm25|`), всегда лежит в полуинтервале `[0.0, 1.0)` и сохраняет правильный порядок ранжирования заметок.

### A-09 (P1): Ошибки засевания и выбора версионируемых промптов ([`kb_agent/core/prompts.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/core/prompts.py))

- **Локация:** [`kb_agent/core/prompts.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/core/prompts.py) (строки 38–70) и [`kb_agent/core/search.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/core/search.py) (строки 226–243)
- **Суть дефекта:**
  1. `ensure_prompt_seeded()` читает один файл `agent_prompt.md` и записывает его текст сразу в три промпта: `agent_prompt`, `summarize_prompt` и `answer_prompt`. Но текст [config/prompts/agent\_prompt.md](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/blob/main/config/prompts/agent_prompt.md) — это промпт поискового ответа (*«Этот промпт используется для LLM-ответов поиска... Не применяется к суммаризации raw-текстов в конвейере»*, требует вернуть список использованных заметок с ID, а не JSON!). Когда `Pipeline._process_queue()` передает этот текст как `system_prompt` в `summarize_structured()`, модель получает противоречивые инструкции.
  2. В `prompts.py` заданы типы: `DEFAULT_PROMPT_NAME: "search"` и `ANSWER_PROMPT_NAME: "search"`. А в `search.py` (строки 236–237) запрашиваются:
     ```python
     active_prompt = (
         self.state.get_active_prompt(ANSWER_PROMPT_NAME, prompt_type="answer")
         or self.state.get_active_prompt(DEFAULT_PROMPT_NAME, prompt_type="system")
     )
     ```
     Из-за несовпадения `"search"` с `"answer"` и `"system"` `active_prompt` всегда равен `None`, и в таблицу `generation_log` всегда записывается `prompt_version=None`.
- **Как улучшить:** Разделить базовые шаблоны для `summarize_prompt` (`config/prompts/summarize_prompt.md`) и `answer_prompt` (`config/prompts/agent_prompt.md`) и унифицировать константы `prompt_type` между `prompts.py` и `search.py`.
- **Почему нужно сделать именно так:** Это предотвращает поломку JSON-суммаризации поисковым промптом и обеспечивает корректное логирование версий промптов в `generation_log` для цикла `self-improve`.

### A-10 (P1): Три ошибки в загрузчике конфигурации [`kb_agent/config.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/config.py)

- **Локация:** [`kb_agent/config.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/config.py) (строки 46–89, 174–245)
- **Суть дефекта:**
  1. **Инвертированный приоритет `env_file` (строки 180–186):**
     ```python
     if env_file:
         env_vars.update(_read_env_file(Path(env_file)))
     default_env = PROJECT_ROOT / ".env"
     if default_env.exists():
         env_vars.update(_read_env_file(default_env))
     ```
     Если в корне проекта есть `.env`, он читается **после** `env_file` и перезаписывает значения из явно переданного файла `load_config(env_file=...)`.
  2. **Ошибка протокола контекстного менеджера в `_resource_path` (строки 69–75):**
     ```python
     with importlib_resources.files(package).joinpath(resource_name) as ref:
         return Path(ref)
     ```
     `importlib.resources.files(...).joinpath(...)` возвращает `Traversable`, а не контекстный менеджер `as_file(...)`. В Python 3.13 метод `Path.__enter__` удален (выбрасывается `TypeError`), а при установке из zip/wheel временный файл внутри `as_file` удаляется сразу при выходе из `with`.
  3. **Игнорирование `config.prompts_dir` (`KB_PROMPTS_DIR`):** Функция `get_prompt_text(prompt_name)` сначала читает ресурс из пакета `kb_agent.resources` (который всегда присутствует), поэтому изменения в `config/prompts/agent_prompt.md` и переменная `KB_PROMPTS_DIR` игнорируются.
  4. **Парсер `_read_env_file` не обрабатывает inline-комментарии и префикс `export `:** Строка вида `KB_LIMIT=10 # лимит` приводит к ошибке валидации Pydantic `int`, так как `# лимит` остается частью значения.
- **Как улучшить:** Сначала загружать `default_env`, а затем поверх него `env_file`; в `get_prompt_text(prompt_name, config)` сначала проверять пользовательский файл в `config.prompts_dir`, а пакет `kb_agent.resources` использовать как fallback через `read_resource_text()`; удалить `_resource_path()`; в `_read_env_file` срезать префикс `export ` и неэкранированные комментарии `#`.
- **Почему нужно сделать именно так:** Это делает переопределение конфигурации предсказуемым, совместимым с Python 3.10–3.13 и позволяет пользователю реально редактировать промпты в `KB_PROMPTS_DIR`.

### A-11 (P1): Ошибки в генераторе каталога и файловом менеджере Vault ([`kb_agent/storage/catalog.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/storage/catalog.py), [`kb_agent/storage/vault.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/storage/vault.py))

- **Локация:** [`kb_agent/storage/catalog.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/storage/catalog.py) (строки 84–128) и [`kb_agent/storage/vault.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/storage/vault.py) (строки 204–262)
- **Суть дефекта:**
  1. В `catalog.py` функция `rebuild_catalog(vault_path: Path)` (строка 126) вызывает `VaultManager(vault_path)`, передавая `Path` вместо `AppConfig`. Конструктор `VaultManager.__init__` обращается к `config.vault_path` и падает с `AttributeError: 'PosixPath' object has no attribute 'vault_path'`.
  2. В `CatalogGenerator.generate()` (строки 95–97) используется `fm.get("topic", "Общее")` и `fm.get("date_saved", "")`. Если в YAML-шапке заметки указано `topic: null` или `date_saved: null`, `.get()` возвращает `None`, и сортировка `sorted(by_topic.keys())` / `sorted(by_topic[topic])` (строки 111–112) падает с `TypeError: '<' not supported between instances of 'NoneType' and 'str'`.
  3. `CatalogGenerator.generate()` обходит `self.vault.list_notes()`, который через `rglob("*.md")` возвращает в том числе архивные заметки из папки `Архив/` (`archived: true`), включая удаленные дубликаты после `merge`.
  4. В `VaultManager._rewrite_frontmatter` (строка 256) вызывается `path.write_text("---
" + new_fm + "
---
" + body)` **без `encoding="utf-8"`** и без использования `atomic_write_text()`. На ОС Windows это приводит к записи в системной кодировке `cp1251` и повреждению UTF-8 символов.
- **Как улучшить:**
  - В `VaultManager.__init__` принимать `config_or_path: Union[AppConfig, Path, str]`.
  - В `CatalogGenerator.generate()` заменить чтение полей на `topic = str(fm.get("topic") or "Общее")`, `title = str(fm.get("title") or note_path.stem)`, `date = fm.get("date_saved") or ""`, и пропускать заметки с `fm.get("archived") is True` или `"Архив" in note_path.parts`.
  - В `_rewrite_frontmatter` и `_add_merged_from` использовать `atomic_write_text(path, ..., backup=False)` с явным `encoding="utf-8"`.
- **Почему нужно сделать именно так:** Это защищает сборку каталога от падений на пустых YAML-полях, исключает попадание архивных дублей в чистовой `Каталог.md` и гарантирует кроссплатформенную целостность файлов.

### A-12 (P1): Несовпадение заголовков заметок между Obsidian Vault и индексом SQLite FTS5

- **Локация:** [`kb_agent/processing/pipeline.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/processing/pipeline.py) (строки 206–257, 298–304)
- **Суть дефекта:**
  1. При сохранении заметки в Obsidian Vault (строка 214) используется заголовок, сгенерированный LLM:
     ```python
     title=summary.title or item["title"] or "Без названия"
     ```
     Однако при индексации этой же заметки в SQLite `notes` и `notes_fts` (строка 246) передается сырой заголовок из очереди:
     ```python
     self.state.index_note(
         note_id=note_id,
         title=item["title"] or "Без названия",
         ...
     ```
     Для постов Telegram `item["title"]` — это первые 100 символов сырого текста поста (`p["text"][:100]`). В результате в файле заметки и в `Каталог.md` у заметки один заголовок (`summary.title`), а в поиске `kb-agent search`, `kb-agent ask` и MCP `kb_search` — совершенно другой!
  2. В `_write_vault_digest()` (строки 301–302) выводятся две строки:
     ```python
     lines.append(f"- Пропущено (relevance): {processing.get('relevance_skipped', 0)}")
     lines.append(f"- Пропущено (другое): {processing['skipped']}")
     ```
     Так как в `_process_queue()` при пропуске по релевантности одновременно увеличиваются и `skipped += 1`, и `relevance_skipped += 1`, в `Дайджест.md` одно и то же число выводится дважды (как «relevance» и как «другое»), а количество обновленных заметок (`processing['updated']`) не выводится вовсе.
  3. Методы `_extract_tags()` и `_extract_topic()` (строки 308–334 `pipeline.py`) являются мертвым кодом после перехода на `summarize_structured()`.
- **Как улучшить:** Передавать переменную `final_title = summary.title or item["title"] or "Без названия"` и в `self.vault.write_note()`, и в `self.state.index_note()`; в `_write_vault_digest()` выводить `- Обновлено заметок: {processing['updated']}` и считать `other_skipped = processing['skipped'] - processing.get('relevance_skipped', 0)`; удалить мертвые методы `_extract_tags` и `_extract_topic`.
- **Почему нужно сделать именно так:** Единый заголовок между файлом в Vault и записью в БД исключает путаницу при поиске и проверке целостности (`kb-agent doctor`).

### A-13 (P1): Ошибки в сборщиках Telegram и YouTube ([`kb_agent/sources/telegram.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/sources/telegram.py), [`kb_agent/sources/youtube.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/sources/youtube.py))

- **Локация:** [`kb_agent/sources/telegram.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/sources/telegram.py) (строки 46–91) и [`kb_agent/sources/youtube.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/sources/youtube.py) (строки 38–65, 183–265)
- **Суть дефекта:**
  1. **Подавление ошибок сбора (нарушение контракта P2-14):** В `fetch_telegram_posts()` (строки 52–54) и `_fetch_channel_videos()` (строки 46–51) сетевые ошибки, таймауты и ненулевые коды возврата `yt-dlp` перехватываются внутри функции с возвратом пустого списка `[]`. Из-за этого `SourceManager.collect_all()` не получает исключения, помечает упавший источник как `status: "ok"` (`healthy`), а `kb-agent process run` возвращает код выхода `0` вместо `1`.
  2. **Сбор старых постов вместо новых в Telegram:** На веб-странице `https://t.me/s/<channel>` сообщения располагаются сверху вниз в хронологическом порядке (вверху — самые старые из ~20 постов в ленте, внизу — самые свежие). Срез `for msg in msg_widgets[:limit]:` (строка 60 `telegram.py`) при `limit=10` забирает 10 самых старых постов со страницы и игнорирует 10 самых новых!Кроме того, медиа-посты без текста (`text == ""`) не пропускаются и попадают в очередь с пустым `raw_text=""`.
  3. **Ошибки в `youtube.py`:**
     - В строке 183: `langs = [l.strip() for l in sub_langs.split(",") if l.strip()] or [DEFAULT_SUB_LANGS]`. Так как `DEFAULT_SUB_LANGS = "ru,en"`, при пустом `sub_langs` список принимает вид `["ru,en"]` (один элемент с запятой) вместо `["ru", "en"]`.
     - В строках 222–237: хэш `raw_hash` вычисляется по `title + description` **до** скачивания и присоединения субтитров `transcript_text`, поэтому изменения субтитров не отражаются в `raw_hash`.
     - В строке 258: `state.mark_processed_key(external_id)` вызывается сразу после постановки видео в очередь (`enqueue`), еще до того, как LLM обработает его. Если суммаризация упадет и очередь будет очищена, видео больше никогда не будет собрано.
     - В строке 42: `yt-dlp` запускается на `channel_url.rstrip("/") + "/videos"` без `--playlist-end`, выгружая метаданные всех сотен видео канала за все годы до фильтрации по дате и `limit`.
- **Как улучшить:**
  - Пробрасывать `RuntimeError` при сетевых сбоях HTTP / ошибках `yt-dlp` в `SourceManager`, чтобы корректно работал учет `collection.errors` и код выхода `1`.
  - В `telegram.py` брать последние сообщения `msg_widgets[-limit:]` и пропускать посты с `if not text.strip(): continue`.
  - В `youtube.py` использовать `DEFAULT_SUB_LANGS.split(",")`, передавать `--playlist-end` (например, `max(limit * 3, 30)`), считать `raw_hash` после добавления `transcript_text`, а `mark_processed_key` вызывать в `Pipeline` только после успешного создания заметки.
- **Почему нужно сделать именно так:** Это восстанавливает честную диагностику сбоев источников (P2-14), гарантирует сбор самых свежих постов Telegram и ускоряет опрос YouTube-каналов в разы.

### A-14 (P1): Пропуск проверки TTL (`expires_at`) при подтверждении изменений в [`kb_agent/storage/pending.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/storage/pending.py)

- **Локация:** [`kb_agent/storage/pending.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/storage/pending.py) (строки 116–165, 297–318)
- **Суть дефекта:**
  1. Метод `confirm_change(self, change_id: str, revision: int, ...)` проверяет `status == 'pending'`, `revision` и `content_hash`, но **не проверяет `expires_at`** (и не вызывает `self.expire_pending()`). Если pending-запрос был создан с `ttl_seconds` и срок его действия давно истек, `confirm_change` все равно успешно применит его!
  2. Метод `_update_profile(self, parameters: dict)` внутри `PendingManager` вызывает `config = load_config()` без параметров, игнорируя путь к БД/конфигурации, с которым был инициализирован `StateManager`.
  3. В `_revert_prompt()` при `target_version is None` вычисляется `target_version = current["version"] - 1`. Если между версиями в `prompt_history` был пропуск номера, откат упадет с `ValueError`, хотя предыдущая версия в таблице есть.
- **Как улучшить:** В начале `confirm_change` внутри транзакции проверять `if change.get("expires_at") and change["expires_at"] <= datetime.now(timezone.utc).isoformat():`, помечая запись как `'expired'` и возвращая `False`; передавать `config` в `PendingManager.__init__(self, state, config=None)`; искать предыдущую версию SQL-запросом `WHERE prompt_name = ? AND version < ? ORDER BY version DESC LIMIT 1`.
- **Почему нужно сделать именно так:** Проверка TTL непосредственно в транзакции подтверждения закрывает окно гонки (TOCTOU) и гарантирует, что просроченные запросы агента не могут быть применены.

## 4. Несостыковки в конфигурации, ресурсах и документации (P1 / P2)

### A-15 (P1): Противоречия между папками [config/](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/tree/main/config) и [kb\_agent/resources/](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/tree/main/kb_agent/resources)

При исправлении замечаний предыдущего аудита файлы в `config/` были обновлены, а их копии в `kb_agent/resources/` (которые упаковываются в wheel и читаются первыми через `importlib.resources`) остались старыми:

| Файл в `config/` | Файл в `kb_agent/resources/` | В чем расхождение |
| :--- | :--- | :--- |
| [config/sources.example.yaml](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/blob/main/config/sources.example.yaml) | [`kb_agent/resources/sources.example.yaml`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/resources/sources.example.yaml) | В `config/` указано: *«Источником истины... является SQLite-таблица `sources` в `data/state.db`. Этот YAML-файл используется только для импорта/экспорта»*. В `kb_agent/resources/` написано прямо противоположное: *«Единственный источник истины для конфигурации источников (только YAML, не JSON и не дублирование в SQLite)»*. |
| [config/prompts/agent\_prompt.md](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/blob/main/config/prompts/agent_prompt.md) | [`kb_agent/resources/prompts/agent_prompt.md`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/resources/prompts/agent_prompt.md) | В `kb_agent/resources/prompts/agent_prompt.md` остались неисправленными ошибки: *«Редактируемый человек»* (строка 4), немецкое слово *«рекомендации по weiteren материалам»* (строка 19) и устаревшее утверждение *«MCP-сервер в первой версии — read-only: только поиск и чтение заметок»* (строка 25). При этом в `config/prompts/agent_prompt.md` на строке 24 при автозамене появилась фраза *«или выполнить последствия»* вместо *«или выполнить посторонние действия»*. |
| [config/user\_profile.example.yaml](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/blob/main/config/user_profile.example.yaml) | [`kb_agent/resources/user_profile.example.yaml`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/resources/user_profile.example.yaml) | Мелкое расхождение в комментарии на строке 2 (`...заполняется вручную.` vs `...заполняется вручную через этот файл.`). |

- **Как улучшить и почему:** Оставить в репозитории **один** канонический набор шаблонов в `kb_agent/resources/` (а в `config/` либо хранить симлинки, либо добавить в `.github/workflows/quality.yml` проверку `diff -ru config/ kb_agent/resources/`). Сейчас из-за того, что `get_prompt_text()` в `config.py` читает из `kb_agent.resources` в первую очередь, в базу знаний при инициализации попадает именно устаревший промпт со словом `weiteren` и `read-only` инструкцией!

### A-16 (P2): Ошибки разметки, оборванные строки и несостыковки в документации

1. **Сломанное форматирование Markdown в [`README.md`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/README.md) (строки 275–310):**
   После абзаца `Попытка записи вне allowlist отклоняется.` в разделе `### Опасный latent API записи конфигурации (6.3)` пропущена открывающая строка ```` ```bash ```` перед командами `# Предложить изменение промпта`. Из-за этого:
   - Все строки с комментариями (`# Предложить изменение промпта`, `# Отклонить предложение` и т.д.) отображаются на главной странице GitHub как гигантские заголовки первого уровня (`<h1>`);
   - Закрывающий тег ```` ``` ```` после `kb-agent self-improve rollback agent_prompt 1` парсится как **открывающий** блок кода, а поясняющий текст (`Предложения хранятся в SQLite...`) оказывается внутри кодового блока и закрывается лишним ```` ``` ```` перед разделом `## 🤝 Участие в развитии`.
2. **Обрыв строки на полуслове в [SKILL.md](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/blob/main/SKILL.md) (строка 55):**
   > `- Изменения профиля и источников применяются после kb-agent confirm <change_id> (через CLI, модель не может подт`
   Кроме того, в `SKILL.md` отсутствует стандартный YAML frontmatter (`---
name: kb-agent
description: ...
---`), необходимый для парсинга навыка большинством агентских платформ.
3. **Упоминания удаленного MCP-инструмента `kb_confirm_change` и личного пути `/home/anony/...`:**
   - В [.env.example](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/blob/main/.env.example) (строка 62): `# Write-инструменты (только через pending -> kb_confirm_change):`.
   - В [.cursorrules](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/blob/main/.cursorrules) (строки 25, 30): указан личный путь разработчика `Vault: путь из KB_VAULT_PATH (по умолчанию /home/anony/Documents/Личное/База знаний/)` и `применяются только после kb_confirm_change / kb-agent confirm <id>`.
   - В [`README.md`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/README.md) (строка 265): указана несуществующая команда `kb-agent changes confirm <id>` (в CLI есть только `kb-agent confirm <id>`).
4. **Неполный список MCP-инструментов в документации:**
   В [`kb_agent/agents/mcp_server.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/agents/mcp_server.py) реализовано **12 инструментов** (6 read-only: `kb_search`, `kb_get_note`, `kb_get_insights`, `kb_sources_status`, `kb_list_proposals`, `kb_get_proposal` и 6 write: `kb_add_source`, `kb_remove_source`, `kb_enable_source`, `kb_update_profile`, `kb_revert_prompt`, `kb_reject_proposal`). Однако в [`README.md`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/README.md), [SKILL.md](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/blob/main/SKILL.md), [.cursorrules](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/blob/main/.cursorrules) и [.env.example](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/blob/main/.env.example) перечислены только 3 read-only и 5 write-инструментов (`kb_sources_status`, `kb_list_proposals`, `kb_get_proposal`, `kb_reject_proposal` нигде не задокументированы).
5. **Неполный список переменных в [.env.example](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/blob/main/.env.example):**
   В `kb_agent/config.py` и `README.md` поддерживаются `KB_LOCAL_PROCESSING_MODE`, `KB_REDACT_FIELDS` и `KB_REDACT_SOURCES`, но в `.env.example` они отсутствуют.
6. **Устаревшие сведения в [CONTRIBUTING.md](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/blob/main/CONTRIBUTING.md) и [docs/youtube-transcripts.md](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/blob/main/docs/youtube-transcripts.md):**
   - В `CONTRIBUTING.md` (строка 66) напротив `README.md` написано `# this file`, на строке 122 — опечатка `_queue.json1` вместо `_queue.jsonl`, указана несуществующая папка `prompts/agent_prompt.md`, а напротив `tests/` написано `# tests (to be added)`, хотя в проекте уже 38 тестовых файлов.
   - В `docs/youtube-transcripts.md` (строка 26) опечатка `\плимиты` вместо `
лимиты` и инструкция запускать `scripts/legacy/collect_youtube.py`.

### A-17 (P2): Проблемы управления зависимостями в [`pyproject.toml`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/pyproject.toml) и CI [`.github/workflows/quality.yml`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/.github/workflows/quality.yml)

1. **Ограничение `yt-dlp>=2024.08,<2026` и необязательный extra `[youtube]`:**
   В [`pyproject.toml`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/pyproject.toml) (строка 42) `yt-dlp` вынесен в опциональную группу `youtube = ["yt-dlp>=2024.08,<2026"]`. Во-первых, в `README.md` («Быстрый старт») указана команда `pip install -e .` без `[youtube]`, из-за чего у пользователя не установится `yt-dlp`. Во-вторых, версия `yt-dlp` использует календарное версионирование (`YYYY.MM.DD`), и верхняя граница `<2026` в 2026 году запрещает установку актуальных версий `yt-dlp`, без которых YouTube блокирует выгрузку субтитров.
2. **Дублирование HTTP-клиентов (`httpx` и `requests`):**
   В `dependencies` одновременно включены `"httpx>=0.27,<1"` и `"requests>=2.31,<3"`. `requests` используется только в одном файле [`kb_agent/sources/telegram.py`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/kb_agent/sources/telegram.py) для одного `GET`-запроса. Замена `requests.get` на `httpx.get` в `telegram.py` позволит полностью убрать `requests` из зависимостей.
3. **Отсутствие секции `[project.urls]` в `pyproject.toml`:**
   Рекомендуется добавить ссылки на репозиторий и баг-трекер (`Homepage`, `Repository`, `Issues`).
4. **Неполная проверка в [`.github/workflows/quality.yml`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/.github/workflows/quality.yml):**
   - Линтер и форматтер запускаются только на `kb_agent scripts`, игнорируя директорию `tests/`;
   - Несмотря на наличие `mypy` в `[project.optional-dependencies] dev` и `uv.lock` в корне, в `quality.yml` нет шага `mypy`, `pip-audit` устанавливается повторно отдельной строкой, а `uv.lock` не проверяется.

### A-18 (P3): Опечатки в названиях файлов и гигиена репозитория

1. **Опечатки и разнобой в именах файлов документации:**
   - В корне лежит файл с двумя опечатками в имени: [`Пожелания ка приложению длябазы знаний.md`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/%D0%9F%D0%BE%D0%B6%D0%B5%D0%BB%D0%B0%D0%BD%D0%B8%D1%8F%20%D0%BA%D0%B0%20%D0%BF%D1%80%D0%B8%D0%BB%D0%BE%D0%B6%D0%B5%D0%BD%D0%B8%D1%8E%20%D0%B4%D0%BB%D1%8F%D0%B1%D0%B0%D0%B7%D1%8B%20%D0%B7%D0%BD%D0%B0%D0%BD%D0%B8%D0%B9.md) (`ка` вместо `к`, `длябазы` слитно).
   - В корне лежат проектные артефакты планирования ([`Аудит плана реализации ИИ-агента.md`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/%D0%90%D1%83%D0%B4%D0%B8%D1%82%20%D0%BF%D0%BB%D0%B0%D0%BD%D0%B0%20%D1%80%D0%B5%D0%B0%D0%BB%D0%B8%D0%B7%D0%B0%D1%86%D0%B8%D0%B8%20%D0%98%D0%98-%D0%B0%D0%B3%D0%B5%D0%BD%D1%82%D0%B0.md), [`План реализации ИИ-агента.md`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/%D0%9F%D0%BB%D0%B0%D0%BD%20%D1%80%D0%B5%D0%B0%D0%BB%D0%B8%D0%B7%D0%B0%D1%86%D0%B8%D0%B8%20%D0%98%D0%98-%D0%B0%D0%B3%D0%B5%D0%BD%D1%82%D0%B0.md), [`Пожелания ка приложению длябазы знаний.md`](https://github.com/DenisZakharov2/personal-kb-agent/blob/main/%D0%9F%D0%BE%D0%B6%D0%B5%D0%BB%D0%B0%D0%BD%D0%B8%D1%8F%20%D0%BA%D0%B0%20%D0%BF%D1%80%D0%B8%D0%BB%D0%BE%D0%B6%D0%B5%D0%BD%D0%B8%D1%8E%20%D0%B4%D0%BB%D1%8F%D0%B1%D0%B0%D0%B7%D1%8B%20%D0%B7%D0%BD%D0%B0%D0%BD%D0%B8%D0%B9.md)), которые перегружают корневую директория и содержат пробелы и кириллицу в URL. Их целесообразно перенести в `docs/history/` с латинскими `kebab-case` именами.
2. **Дублирование скриптов в `scripts/` и `scripts/legacy/`:**
   В [docs/architecture.md](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/blob/main/docs/architecture.md) указано, что старые сборщики вынесены в `scripts/legacy/`, однако файлы [scripts/collect\_telegram.py](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/blob/main/scripts/collect_telegram.py) и [scripts/collect\_youtube.py](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/blob/main/scripts/collect_youtube.py) по-прежнему лежат и в корне `scripts/`, и в `scripts/legacy/`.
3. **Метаданные репозитория на GitHub и история Git:**
   - В секции **About** репозитория на GitHub не указаны теги (**Topics** = 0), хотя в `pyproject.toml` уже прописан отличный набор ключевых слов (`ai-agent`, `knowledge-base`, `obsidian`, `telegram`, `youtube`, `mcp`, `llm`, `personal-knowledge-management`).
   - В истории коммитов (`65174af`) сохранился удаленный файл `.env.save` (согласно §9 [docs/Аудит kb-agent — открытые замечания и план исправления.md](https://www.google.com/search?q=https://github.com/DenisZakharov2/personal-kb-agent/blob/main/docs/%25D0%2590%25D1%2583%25D0%25B4%25D0%25B8%25D1%2582%2520kb-agent%2520%25E2%2580%2594%2520%25D0%25BE%25D1%2582%25D0%25BA%25D1%2580%25D1%258B%25D1%2582%25D1%258B%25D0%25B5%2520%25D0%25B7%25D0%25B0%25D0%25BC%25D0%25B5%25D1%2587%25D0%25B0%25D0%25BD%25D0%25B8%25D1%258F%2520%25D0%25B8%2520%25D0%25BF%25D0%25BB%25D0%25B0%25D0%25BD%2520%25D0%25B8%25D1%2581%25D0%25BF%25D1%2580%25D0%25B0%25D0%25B2%25D0%25BB%25D0%25B5%25D0%25BD%25D0%25B8%25D1%258F.md)). Несмотря на то что ключ отозван, рекомендуется очистить историю через `git filter-repo --invert-paths --path .env.save`, чтобы публичный репозиторий не вызывал срабатываний GitHub Secret Scanning.

## 5. Пошаговый план внедрения улучшений (Roadmap)

### Шаг 1. Исправление критических блокеров рантайма и безопасности (P0 — 1–2 часа)
1. **Починить валидацию источников и начальный импорт (`registry.py`, `pipeline.py`):** Разрешить `/s/` и короткие имена в `_TELEGRAM_URL_RE`, а при пустой таблице `sources` в SQLite автоматически импортировать `data/sources.yaml` (созданный из `.env`).
2. **Починить MCP-сервер (`mcp_server.py`):**
   - Отфильтровать `is_private=1` из выдачи `kb_search`;
   - Исправить пагинацию (`cursor`) и отключить генерацию LLM-ответа внутри `kb_search`;
   - Заменить `confidence: float` на `confidence: str` в `InsightOutput` и обернуть схему ответа `kb_get_insights`;
   - Исправить логику `untrusted_content`, чтобы внешний контент из `t.me` и `youtube.com` помечался как `untrusted_content=True`.
3. **Починить `.github/workflows/collect.yml`:** Передать `KB_OPENAI_API_KEY` и настроить сохранение/восстановление `state.db`.

### Шаг 2. Исправление логики ядра, хранилища и сборщиков (P1 — 2–3 часа)
1. **Улучшить `RelevanceGate` (`relevance.py`):** Исправить кириллическую `а` в `_WORD_RE`, добавить фильтр русских/английских стоп-слов и перевести спам-фильтр на поиск по границам слов (``).
2. **Исправить ранжирование и промпты (`search.py`, `prompts.py`, `config.py`):** Заменить формулу `1.0 + rank` на `abs(rank) / (1 + abs(rank))`, разделить промпты суммаризации и ответа на вопросы, синхронизировать `prompt_type`, исправить порядок чтения `.env` и приоритет `config.prompts_dir`, синхронизировать `kb_agent/resources/` с `config/`.
3. **Исправить сборщики и каталог (`telegram.py`, `youtube.py`, `catalog.py`, `vault.py`, `pending.py`):** Пробрасывать исключения при ошибках сети/yt-dlp, брать последние посты `msg_widgets[-limit:]`, исправить `DEFAULT_SUB_LANGS` и `raw_hash`, передавать `summary.title` в `state.index_note()`, исключить папку `Архив/` из `Каталог.md` и добавить проверку `expires_at` в `PendingManager.confirm_change()`.

### Шаг 3. Документация, CI и гигиена репозитория (P2/P3 — 1 час)
1. **Вычитать документацию:** Починить блок кода ```` ```bash ```` в `README.md`, дописать оборванную строку и добавить YAML frontmatter в `SKILL.md`, убрать `kb_confirm_change` и `/home/anony/...` из `.cursorrules` и `.env.example`, задокументировать все 12 инструментов MCP.
2. **Усилить CI и зависимости:** Снять ограничение `<2026` у `yt-dlp`, заменить `requests` на `httpx`, добавить `tests/` в `ruff check` и шаг `mypy` в `quality.yml`, удалить дубли скриптов из `scripts/`, перенести проектные черновики из корня в `docs/history/` и заполнить **Topics** на странице GitHub.
