# Аудит проекта personal-kb-agent: открытые замечания и план исправлений

> **Статус:** это снимок состояния **до** исправлений (для истории). Замечания
> R-01…R-32 закрыты в коммите `fix: устранены ошибки и несостыковки из аудита`;
> актуальное поведение описано в `docs/architecture.md`, `docs/workflow.md`, `docs/catalog.md`.
> Отдельно закрыта утечка ключа — см. §9.

**Дата:** 2026-11-04  
**Репозиторий:** `github.com/DenisZakharov2/personal-kb-agent`, ветка `main`  
**Коммит на момент аудита:** `c3fe165` (`security: убран API-ключ из .env.save`)  
**Область аудита:** `kb_agent/`, `scripts/`, `Скрипт/`, `config/`, `.github/workflows/`, документация  
**Вне рамок отчёта:** утечка API-ключа — закрыта (ключ отозван в кабинете Kimi, файл удалён из индекса и из `origin/main`). Остальные правки перечислены в §3.

---

## 1. Как проверялось и ограничения

| Шаг | Что даёт |
|---|---|
| Чтение всех 32 файлов кода + 12 файлов документации/конфигов | понимание фактической архитектуры |
| `python -m compileall kb_agent scripts` | синтаксическая проверка (ОК) |
| AST-анализ (свои скрипты) | неиспользуемые импорты (0 после правок), неопределённые имена, undefined-name эвристика |
| Сборка wheel (`pip wheel`) + `unzip -l` | контроль состава пакета (до/после правки `pyproject.toml`) |
| Рантайм-прогон с заглушками `pydantic/typer/yaml/httpx/mcp/rich/bs4/requests` | проверка каталога, дедупликации, `revert_prompt`, `kb_get_note`, форматирования ответа |
| `sqlite3` из stdlib на реальной схеме | проверка FTS5-запросов, миграций, граничных значений `limit` |
| `git grep` по секретам (`sk-`, `ghp_`, `AKIA`, приватные ключи, JWT) | секретов в трекаемых файлах нет |

**Ограничения среды:** нет доступа к PyPI, поэтому `ruff`, `pytest`, `mypy` и целевые версии зависимостей запустить не удалось; вместо них — compileall, AST-анализ, стабы и ручные рантайм-проверки. Все дефекты ниже помечены как **[проверено]** (воспроизведено запуском) или **[анализ]** (вывод из кода).

---

## 2. Резюме

| Уровень | Открыто | Суть |
|---|---|---|
| **P1 — ломает заявленную функциональность** | 6 | падение поиска на кавычке; инсайты, самоулучшение и откат промптов фактически не работают; Relevance Gate несовместим с шаблоном профиля; не создаётся `sources.yaml`; `kb_search` без содержимого |
| **P2 — данные и БД** | 5 | невалидный YAML frontmatter; неидемпотентная миграция; `note_id` с пробелом; тема по эвристике; нет верхней границы `limit` |
| **P3 — конфигурация, окружение, CI** | 5 | три имени переменной для YouTube; секреты CI в `.env`, который никто не читает; CI не связан с пайплайном |
| **P4 — дубли и мёртвый код** | 5 | два писателя заметок; три поколения сборщиков; неиспользуемые Typer-приложение и хелперы |
| **P5 — документация против кода** | 4 | JSONL-архитектура в доках; frontmatter в примере ≠ фактический; «✅» у заглушек |
| **P6 — качество кода** | 7 | pydantic-v1 API; жёсткий таймаут LLM; легаси-скрипты, перезаписывающие код |
| **Всего** | **32** | |

**Итого уже исправлено:** 11 дефектов (включая 4 функциональных бага и поломку сборки пакета), 23 опечатки/лексических дефекта, 9 мест расхождения документации с кодом.

---

## 3. Уже исправлено (в рабочем дереве, не закоммичено)

| # | Что | Где | Как проверено |
|---|---|---|---|
| F-01 | `kb_get_note` всегда отвечал «не найдено»: `state.search("")` возвращает `[]` | `agents/mcp_server.py`, новый `storage/state.py::get_note` | рантайм: `## Заметка: Note A` |
| F-02 | `update check`: дубли колонок в SQL → пара дубля `('A','A')` | `core/update.py` | рантайм: `('A','B')` |
| F-03 | `Каталог.md` включал сам себя и `Дайджест.md` | `storage/catalog.py` | рантайм: «Всего заметок: 1» |
| F-04 | `revert_prompt` при ненайденной версии давал статус `applied` | `storage/pending.py` | рантайм: `status: failed` |
| F-05 | `NameError`-риск: `Optional` не импортирован | `processing/pipeline.py:39` | compileall + AST |
| F-06 | `ask` вызывал LLM дважды, `search` — вопреки своему описанию | `cli/commands/ask.py`, `core/search.py` | импорт/чтение |
| F-07 | Wheel содержал 2 файла вместо 43 (`packages = ["kb_agent"]`) | `pyproject.toml` | пересборка wheel: все подпакеты на месте |
| F-08 | `typer[all]` — extra удалён в новых typer | `pyproject.toml` | анализ |
| F-09 | 12 неиспользуемых импортов | 9 модулей | AST: 0 файлов |
| F-10 | Документация утверждала «MCP read-only, 3 инструмента», «источники только через pending», «`[[ссылки]]` в заметках», `collect_telegram.py catalog`, `_done.txt` | README, SKILL.md, `.cursorrules`, `docs/workflow.md` | сверка с кодом |
| F-11 | 23 опечатки/иноязычных вставки (`Strukturujte`, `novos`, `restructures`, `Gruppiert`, `weitere`, `метаяне`, `Etap`, `сырый`, `сфоих`, `с помощь`, `две уровни`, `RelevantResult`, `кодированияwrite` и др.) | README, SKILL, agent_prompt.md, docs/*, План, Пожелания, config.py, relevance.py, llm_client.py, prepare_stage4_write.sh | `grep` по шаблонам: не найдено |

---

## 4. Открытые замечания

Формат: **ID** → где → симптом → подтверждение → влияние → рекомендация → трудоёмкость (S ≤ 1 ч, M ≤ полдня, L > полдня).

### P1 — функциональные дефекты

#### R-01. Поиск падает на кавычке в запросе **[проверено]**
- **Где:** `kb_agent/storage/state.py:288` (`StateManager.search`).
- **Симптом:** `kb-agent search '"'` и то же через MCP `kb_search` роняют команду: `sqlite3.OperationalError: unterminated string`. Слова оборачиваются в кавычки как есть, без экранирования: `" OR ".join(f'"{w}"' ...)`.
- **Подтверждение:** запрос `'"'` → `EXCEPTION OperationalError unterminated string`; при этом `'python'`, `'a AND b'`, `'NEAR('` отрабатывают.
- **Влияние:** необработанное исключение в CLI и в MCP-инструменте; любой пользовательский/AI-запрос с `"`, `-`, `*` может уронить процесс вместо пустого результата.
- **Рекомендация:** санитизировать токены и оборачивать в кавычки с удвоением:
```python
raw = query.replace('"', ' "" ')
words = re.findall(r"[\w\-]+", raw, flags=re.UNICODE)
fts_query = " OR ".join(f'"{w}"' for w in words) or None
# и обернуть выполнение в try/except sqlite3.OperationalError -> []
```
- **Трудоёмкость:** S.

#### R-02. Инсайты — заглушки, связи между заметками не создаются **[анализ]**
- **Где:** `kb_agent/core/insights.py:63` (`_find_repeating_themes`), `:70` (`_find_contradictions`), `kb_agent/storage/state.py:322` (`add_link`).
- **Симптом:** оба метода возвращают пустой список, в коде стоит комментарий «заглушка». `add_link()` не вызывается нигде, кроме собственного определения → таблица `note_links` всегда пуста, а `_find_existing_links` строит инсайты только из неё.
- **Подтверждение:** `grep -rn 'add_link'` → единственное вхождение = определение метода.
- **Влияние:** `kb-agent insights` и MCP `kb_get_insights` всегда отвечают «Инсайтов пока нет»; требование из «Пожеланий» («выявлять скрытые зависимости») не выполнено, хотя README ставит Этапу 3 «✅».
- **Рекомендация:** минимум — реализовать повторяющиеся темы SQL-запросом по `notes_fts` (разбор тегов через `json_each` после перевода тегов в JSON) и вызывать `add_link` из конвейера при совпадении тегов; либо снять «✅» и описать статус как «заглушка».
- **Трудоёмкость:** M.

#### R-03. Промпты не версионируются → `self-improve rollback` и `kb_revert_prompt` нечего откатывать **[анализ]**
- **Где:** `kb_agent/config.py:63` (`prompts_dir`), `config/prompts/agent_prompt.md`, `kb_agent/core/self_improvement.py:172` (`save_prompt`), `:143` (`list_proposals` → `[]`).
- **Симптом:** `prompts_dir` только объявлен и валидируется, но не читается ни одним модулем; `agent_prompt.md` используется лишь как документация; `prompt_history` заполняется единственным вызовом `save_prompt()` внутри `apply_prompt_proposal()`, который не вызывается ни из CLI, ни из MCP; `list_proposals()` возвращает `[]`.
- **Подтверждение:** `grep -rn 'prompts_dir'` → только `config.py`; `grep -rn 'save_prompt'` → определение + один вызов из `self_improvement.py`.
- **Влияние:** `kb-agent self-improve rollback <prompt> <version>` всегда падает с «Активная версия промпта не найдена»; MCP `kb_revert_prompt` всегда завершается статусом `failed` (после F-04 — честно, но бесполезно); «самоулучшение» из README не работает.
- **Рекомендация:** при первом запуске (`init_db`/`interview init`) импортировать `agent_prompt.md` в `prompt_history` как `version=1, is_active=1`; читать активную версию из БД при формировании промптов; добавить CLI `self-improve propose/apply`.
- **Трудоёмкость:** M.

#### R-04. Relevance Gate несовместим с шаблоном профиля **[проверено, анализ]**
- **Где:** `kb_agent/core/relevance.py:26-27` против `config/user_profile.example.yaml:7,10,17`.
- **Симптом:** гейт читает `profile["topics"]` и `profile["goal"]` из секции `user`, а шаблон кладёт `goals` (список) и `topics` на верхний уровень, вне `user`.
- **Подтверждение:** `_load_profile()` (`pipeline.py`) возвращает `data.get("user", {})` → `{'name': None}`; `RelevanceGate({'name': None})` → «профиль не задан, материал принят» для любого текста.
- **Влияние:** при работе по шаблону фильтр отсева не работает вообще — в базу попадает всё, включая рекламу (спам-ключевые слова ловятся только по списку в `sources/telegram.py`).
- **Рекомендация:** привести шаблон к структуре, которую пишет интервью (`user: {goal, tasks, topics, sources, frequency, system}`), и добавить валидацию профиля при загрузке (логировать отсутствие `goal`/`topics`).
- **Трудоёмкость:** S.

#### R-05. `data/sources.yaml` не создаётся автоматически **[анализ]**
- **Где:** `kb_agent/sources/registry.py:140` (`ensure_sources_file`) — функция не вызывается.
- **Симптом:** на чистой установке файла источников нет; `SourceRegistry.load()` пишет `warning` и возвращает `{}`.
- **Влияние:** `kb-agent process run` на свежей установке «успешно» собирает 0 материалов; пользователь видит только warning в логе.
- **Рекомендация:** вызывать `ensure_sources_file()` в `Pipeline.run()`/`SourceManager.__init__`; при пустом реестре печатать в CLI явное сообщение с подсказкой `kb-agent sources add ...`.
- **Трудоёмкость:** S.

#### R-06. MCP `kb_search` не возвращает содержимое заметок **[анализ]**
- **Где:** `kb_agent/agents/mcp_server.py`, ветка `kb_search`.
- **Симптом:** в ответ попадают только `title`, `note_id`, `tags`, `topic`; поле `content` отбрасывается.
- **Влияние:** внешний агент (Cursor/Claude Code) вынужден делать по одному `kb_get_note` на каждую заметку, чтобы ответить по существу; это противоречит описанию инструмента «Поиск по заметкам базы знаний».
- **Рекомендация:** добавить в ответ фрагмент `content[:500]` (или параметр `snippet_len`), сохранив ограничение размера.
- **Трудоёмкость:** S.

---

### P2 — данные и БД

#### R-07. YAML frontmatter собирается f-строками → возможен невалидный YAML **[анализ]**
- **Где:** `kb_agent/storage/vault.py:88-94` (и дубль `processing/kb_writer.py:92-98`).
- **Симптом:** значения подставляются без кавычек и экранирования: `title: Итоги: как ускорить работу` — двоеточие с пробелом делает документ невалидным; теги пишутся python-repr (`tags: ['ai', 'n8n']`).
- **Влияние:** `CatalogGenerator._parse_frontmatter` глотает `yaml.safe_load`-исключение и молча возвращает `{}` → заметка выпадает из тематической группы, получает тему «Общее» и заголовок-имя файла. Диагностики нет.
- **Рекомендация:** `yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False, default_flow_style=None)`, плюс `logger.warning` при ошибке парсинга в каталоге.
- **Трудоёмкость:** S.

#### R-08. Миграция №10 не идемпотентна **[анализ]**
- **Где:** `kb_agent/storage/state.py:135-150`.
- **Симптом:** `CREATE TABLE IF NOT EXISTS feedback_new` → `INSERT ... SELECT * FROM feedback` → `DROP TABLE feedback` → `RENAME`. При сбое между `DROP` и `RENAME` (или при уже существующей `feedback_new`) повторный `init_db()` падает навсегда: `no such table: feedback` / дубли строк.
- **Влияние:** БД приходится удалять вручную; потеря очереди и индекса.
- **Рекомендация:** выполнять миграции в одной транзакции (`BEGIN IMMEDIATE ... COMMIT`), проверять `PRAGMA table_info(feedback)` вместо пересоздания таблицы, либо перейти на `PRAGMA user_version` без DROP/RENAME.
- **Трудоёмкость:** M.

#### R-09. `note_id` содержит пробел **[проверено]**
- **Где:** `kb_agent/processing/pipeline.py:137`: `f"{item['received_at'][:13]}_{uuid4().hex[:8]}"`.
- **Симптом:** `received_at = "2026-11-04 10:45:00"` → `note_id = "2026-11-04 10_ab12cd34"`. Подтверждено; этот же формат зафиксирован в примере `SKILL.md`.
- **Влияние:** пробел в идентификаторе неудобен для URL, якорей Obsidian, шелл-команд и JSON-путей; легко получить «две» заметки при копировании без кавычек.
- **Рекомендация:** `datetime.fromisoformat(received_at).strftime('%Y-%m-%dT%H')` → `2026-11-04T10_ab12cd34`.
- **Трудоёмкость:** S (плюс миграция существующих заметок — опционально).

#### R-10. Тематика заметки определяется эвристикой, а не профилем **[анализ]**
- **Где:** `kb_agent/processing/pipeline.py:251` (`_guess_topic`), docstring: «заглушка, будет уточняться через профиль».
- **Симптом:** тема выбирается из 4 жёстких наборов подстрок (`python/код/...` → «Программирование» и т. п.); `RelevanceGate.topics` не используется, LLM не спрашивают о теме, хотя промпт требует «Теги».
- **Влияние:** все заметки сваливаются в 4 папки, тематические папки из профиля не создаются — расходятся с README («тематические папки из профиля»).
- **Рекомендация:** просить тему у LLM (доп. строка в промпте `Тема: ...`) с fallback на эвристику; валидировать по списку `profile['topics']`.
- **Трудоёмкость:** S–M.

#### R-11. Нет верхней границы `limit` в поиске **[проверено]**
- **Где:** `StateManager.search` / MCP-схема `kb_search` (`maximum: 20` задекларирован, но не применяется).
- **Симптом:** `state.search(..., limit=1000)` возвращает 1000 строк; строка `"5"` тоже принимается.
- **Влияние:** внешний агент может одним вызовом вытянуть всю базу и переполнить контекст LLM (плюс расход токенов).
- **Рекомендация:** `limit = max(1, min(int(limit), 20))` в `StateManager.search`.
- **Трудоёмкость:** S.

---

### P3 — конфигурация, окружение, CI

#### R-12. Три несовместимых имени для YouTube-источников **[анализ]**
- **Где:** `config.py:128` (`KB_YOUTUBE_CHANNELS`), `scripts/collect_youtube.py:45` (`KB_YT_CHANNEL_URL`), `.github/workflows/collect.yml:29` (`KB_YT_CHANNEL_URL`), `config/channels.example.json` (не читается никем), `.env.example:14` и `.env.save:2` (разные имена).
- **Симптом:** пакет ждёт список URL в `KB_YOUTUBE_CHANNELS`, легаси-скрипт — один URL в `KB_YT_CHANNEL_URL`.
- **Влияние:** настройка одного компонента молча не влияет на другой; пользователь заполняет `.env.example`, а `scripts/` этого не видит.
- **Рекомендация:** оставить одно имя (`KB_YOUTUBE_CHANNELS`, список), в скриптах читать его и брать первый/все элементы; `channels.example.json` удалить как неиспользуемый.
- **Трудоёмкость:** S.

#### R-13. CI пишет секреты в `.env`, который никто не читает **[анализ]**
- **Где:** `.github/workflows/collect.yml:24-29`, `scripts/collect_*.py` (только `os.getenv`).
- **Симптом:** шаг «Configure environment» делает `cp .env.example .env` и дописывает `KB_*` в файл; но `python-dotenv` запрещён планом и не используется, скрипты читают только реальное окружение.
- **Влияние:** шаг бесполезен, сбор в CI идёт с дефолтами (`DEFAULT_CHANNELS`, `DEFAULT_CHANNEL_URL`) — то есть «не туда».
- **Рекомендация:** писать в `$GITHUB_ENV` (`echo "KB_TELEGRAM_CHANNELS=${{ secrets.X }}" >> "$GITHUB_ENV"`).
- **Трудоёмкость:** S.

#### R-14. CI не связан с приложением **[анализ]**
- **Где:** `.github/workflows/collect.yml`.
- **Симптом:** workflow ставит только `yt-dlp requests beautifulsoup4`, запускает легаси-скрипты и архивирует `scripts/_queue.jsonl` + `_transcripts/`; приложение же работает с SQLite `raw_queue`, собирает через `kb_agent/sources/*` и без LLM-ключа.
- **Влияние:** артефакты CI никто не потребляет; расписание `0 10 * * *` создаёт иллюзию автоматического наполнения базы, которого нет.
- **Рекомендация:** либо перевести workflow на `pip install -e .` + `kb-agent process run` с секретом `KB_OPENAI_API_KEY` и кэшем `data/state.db`, либо честно переименовать его в «legacy queue collector» и убрать из README упоминание cron.
- **Трудоёмкость:** M.

#### R-15. `.env.example` не документирует поддерживаемые переменные **[анализ]**
- **Где:** `.env.example` против `config.py:113-132`.
- **Симптом:** поддерживаются `KB_DATA_DIR`, `KB_DB_PATH`, `KB_SOURCES_PATH`, `KB_PROFILE_PATH`, `KB_PROMPTS_DIR`, но в шаблоне их нет; при этом в шаблоне есть `KB_LOG_PATH` с путём, отличным от дефолта пакета (`data/kb_agent.log`).
- **Влияние:** пользователь не знает о возможности переопределить пути; расхождение дефолтов путает при отладке.
- **Рекомендация:** синхронизировать список переменных с `env_mapping` и выровнять дефолт `KB_LOG_PATH`.
- **Трудоёмкость:** S.

#### R-16. Конфиг источников заявлен в двух местах **[анализ]**
- **Где:** `config.py` (`data/sources.yaml`) против `docs/architecture.md`/`docs/workflow.md`, где фигурируют `scripts/_queue.jsonl` и «channels.json».
- **Симптом:** документация описывает JSONL-хранилище и `channels.json`, которых в коде нет.
- **Влияние:** новый участник/агент идёт по неверному пути.
- **Рекомендация:** обновить `docs/architecture.md` и `docs/workflow.md` до фактической схемы (SQLite `raw_queue`, `data/sources.yaml`, `data/user_profile.yaml`, `data/state.db`).
- **Трудоёмкость:** S.

---

### P4 — дубли и мёртвый код

#### R-17. Два независимых писателя заметок **[проверено]**
- **Где:** `kb_agent/storage/vault.py` (используется) и `kb_agent/processing/kb_writer.py` (не используется).
- **Симптом:** `KBWriter.write_note` раскладывает заметки по папкам `Телеграмм/` и `YouTube/`, `VaultManager.write_note` — по тематическим папкам и с атомарной записью через `.tmp`. Обе пишут один и тот же frontmatter (дублирование логики), но по-разному.
- **Влияние:** риск, что кто-то «починит» неиспользуемый класс; расхождение с `docs/catalog.md`, который описывает папки Телеграмм/YouTube.
- **Рекомендация:** удалить `kb_writer.py` либо сделать `VaultManager` единственной реализацией и вынести общий сериализатор frontmatter.
- **Трудоёмкость:** S.

#### R-18. Неиспользуемые функции сбора в пакете **[проверено]**
- **Где:** `kb_agent/sources/telegram.py:101` (`collect_telegram`), `kb_agent/sources/youtube.py:145` (`collect_youtube`).
- **Симптом:** вызываются только `collect_telegram_source` / `collect_youtube_source`; функции для `config.telegram_channels` / `config.youtube_channels` остались от ранней версии.
- **Влияние:** мёртвый код + иллюзия, что настройки `KB_TELEGRAM_CHANNELS`/`KB_YOUTUBE_CHANNELS` работают (фактически сбор идёт только по `data/sources.yaml`).
- **Рекомендация:** удалить; при желании поддержать env — реализовать импорт env → `sources.yaml` в `SourceRegistry.load()`.
- **Трудоёмкость:** S.

#### R-19. Три поколения сборщиков одновременно **[анализ]**
- **Где:** `scripts/collect_telegram.py`, `scripts/collect_youtube.py` (JSONL, `argparse`), `Скрипт/collect.py`, `Скрипт/run.sh` (абсолютные пути `/home/anony/Documents/...`), `kb_agent/sources/*` (SQLite).
- **Симптом:** разный формат хранения, разная логика спама, разные переменные окружения, дублирование парсинга `t.me/s/`.
- **Влияние:** багфикс нужно делать трижды; `Скрипт/` неработоспособен на другой машине.
- **Рекомендация:** оставить `kb_agent/sources/` как единственную реализацию, `scripts/` — помечены как `legacy/` с README, `Скрипт/` удалить.
- **Трудоёмкость:** M.

#### R-20. Неиспользуемое Typer-приложение **[анализ]**
- **Где:** `kb_agent/cli/commands/pending.py:16`.
- **Симптом:** модуль создаёт `app = typer.Typer(...)` и регистрирует в нём команду `pending`, но `cli/main.py` регистрирует функцию `pending_command` напрямую; `app` нигде не импортируется.
- **Влияние:** путаница при добавлении новых подкоманд (легко зарегистрировать в «мёртвом» приложении).
- **Рекомендация:** удалить `app`; либо наоборот подключить его через `add_typer` и завести подкоманды `pending list/confirm`.
- **Трудоёмкость:** S.

#### R-21. Копипаста `_output_json` в 6 модулях CLI **[анализ]**
- **Где:** `cli/commands/{ask,confirm,feedback,interview,pending,process,self_improve,sources}.py`.
- **Симптом:** одинаковая функция в 8 файлах; в `interview.py` она использует `__import__("json")` вместо импорта и объявлена ниже места первого использования.
- **Влияние:** расхождения при доработке вывода (например, добавлении `indent`/сортировки).
- **Рекомендация:** вынести в `kb_agent/cli/output.py`.
- **Трудоёмкость:** S.

---

### P5 — документация против кода

| ID | Где | Что заявлено | Фактически |
|---|---|---|---|
| **R-22** | `docs/architecture.md` (раздел «Хранение данных», «Почему нет локальной LLM») | `_queue.jsonl`, `_transcripts/`, анализ делает Hermes Agent, локальной LLM нет | SQLite `raw_queue` + `notes_fts`, LLM вызывается из `processing/pipeline.py` через `LLMClient` |
| **R-23** | `docs/catalog.md` (пример frontmatter) | `channel`, `thumbnail`, `duration`, `transcript`, `tags` | `vault.py` пишет `source`, `source_id`, `external_id`, `url`, `date_saved`, `published_at`, `received_at`, `note_id`, `previous_note_id`, `frontmatter_schema_version`, `title`, `tags`, `topic` |
| **R-24** | `README.md` («Возможности по этапам») | Этап 3 «Поиск, ответы, дубликаты, инсайты» — ✅; Этап 5 «Обратная связь, самоулучшение» — ✅ | инсайты и самоулучшение — заглушки (R-02, R-03) |
| **R-25** | `docs/workflow.md` (шаги 2–6) | двухэтапный ручной процесс: скрипты → «Hermes Agent» пишет заметки → `_done.txt` | единый конвейер `kb-agent process run`; состояние — таблица `processed`, файлов `_done.txt` нет |

**Рекомендация:** привести `docs/*` к фактическому поведению либо явно пометить разделы как «историческое описание этапов 1–2». Трудоёмкость: S (документация), но требует решения, что считать эталоном — доки или код.

---

### P6 — качество кода и легаси

#### R-26. Pydantic-v1 API при `pydantic>=2` **[анализ]**
- **Где:** `kb_agent/config.py:86` (`@validator`), `:92` (`class Config`).
- **Влияние:** DeprecationWarning в рантайме и риск поломки при pydantic 3; тесты/линтеры могут падать на предупреждениях.
- **Рекомендация:** `@field_validator(..., mode="after")` + `model_config = ConfigDict(arbitrary_types_allowed=True)`.
- **Трудоёмкость:** S.

#### R-27. `llm_provider` не используется, таймаут жёсткий **[анализ]**
- **Где:** `kb_agent/processing/llm_client.py:46` (`self.provider = config.llm_provider`), `:92` (`timeout=60.0`).
- **Симптом:** провайдер сохраняется и нигде не читается; нет ретраев/backoff, таймаут не настраивается; Anthropic заявлен в `.env.example`, но требует OpenAI-совместимого прокси.
- **Влияние:** при 429/503 материал сразу помечается `failed` (retry-поля `attempts`/`next_retry_at` есть в схеме, но не используются конвейером).
- **Рекомендация:** использовать `provider` для выбора заголовков/дефолтов; добавить 2–3 ретрая с backoff и `KB_LLM_TIMEOUT`.
- **Трудоёмкость:** M.

#### R-28. Мелочи в `cli/main.py` **[анализ]**
- **Где:** `kb_agent/cli/main.py:32` (`update_app as update_app`), docstring модуля, отсутствие двух пустых строк перед `if __name__`.
- **Рекомендация:** убрать бессмысленный алиас, дополнить docstring командами `sources/feedback/pending/confirm/self-improve/mcp-serve`.
- **Трудоёмкость:** S.

#### R-29. `cli/commands/interview.py` **[анализ]**
- **Где:** `:57-58` — двойной вызов `state.init_db()`; `:89` — `_output_json` объявлен после использования; `:91` — `__import__("json")`.
- **Рекомендация:** один вызов `init_db()`, хелпер вывода вынести в общий модуль (см. R-21).
- **Трудоёмкость:** S.

#### R-30. `scripts/run.sh` **[анализ]**
- **Где:** `scripts/run.sh` (5 строк).
- **Симптом:** нет shebang; `mkdir -p scripts` выполняется после `cd` в корень (бессмысленно); `"$@"` передаётся всем трём командам — `--limit` сломает `collect_youtube collect`, `--channel-url` сломает `transcripts`.
- **Рекомендация:** `#!/usr/bin/env bash`, `set -euo pipefail`, отдельные аргументы на команду или полный переход на `kb-agent`.
- **Трудоёмкость:** S.

#### R-31. `scripts/init_stage1.sh` перезапишет актуальные файлы **[анализ]**
- **Где:** `scripts/init_stage1.sh` (963 строки).
- **Симптом:** генерирует каркасные версии `pyproject.toml`, `config.py`, `storage/state.py`, `cli/main.py` и др. (Этап 1) — они уже давно переписаны вручную.
- **Влияние:** запуск скрипта на текущем репозитории откатит код к состоянию Этапа 1.
- **Рекомендация:** перенести в `scripts/legacy/` с предупреждением в первой строке или удалить (история есть в git).
- **Трудоёмкость:** S.

#### R-32. Метрики смешивают оценки и сообщения **[анализ]**
- **Где:** `kb_agent/feedback/metrics.py:73`: `recent_feedback = ratings[:10] + messages[:10]`.
- **Симптом:** два отдельных списка конкатенируются без сортировки по времени — в CLI «Последние отзывы» порядок некорректен относительно дат.
- **Рекомендация:** брать единый запрос `ORDER BY created_at DESC LIMIT 10` и размечать тип записи.
- **Трудоёмкость:** S.

---

## 5. Матрица несостыковок

| Сущность | Заявлено | Фактически | Файлы |
|---|---|---|---|
| Источники Telegram | `KB_TELEGRAM_CHANNELS` (env) | `data/sources.yaml` | `.env.example:11`, `config.py:74`, `sources/manager.py` |
| Источники YouTube | `KB_YOUTUBE_CHANNELS` / `KB_YT_CHANNEL_URL` / `channels.example.json` | `data/sources.yaml` | `config.py:128`, `scripts/collect_youtube.py:45`, `.github/workflows/collect.yml:29`, `config/channels.example.json` |
| Конфиг источников | `config/sources.yaml` | `data/sources.yaml` | `.cursorrules`, `config.py:22` |
| Профиль | `config/user_profile.yaml` | `data/user_profile.yaml`; шаблон имеет другую структуру (R-04) | `.cursorrules`, `config.py:23`, `config/user_profile.example.yaml` |
| Хранение очереди | `_queue.jsonl` (доки, CI) | SQLite `raw_queue` | `docs/architecture.md`, `docs/workflow.md`, workflow |
| Папки заметок | `Телеграмм/`, `YouTube/` (доки, `kb_writer.py`) | тематические папки (`vault.py`) | `docs/catalog.md`, `processing/kb_writer.py`, `storage/vault.py` |
| Frontmatter | `channel/thumbnail/duration/transcript` | `source_id/external_id/note_id/topic/...` | `docs/catalog.md`, `storage/vault.py` |
| Ссылки между заметками | `[[ссылки]]` в заметках | ссылок нет, `note_links` пуста | `README.md`, `core/insights.py` |
| Промпт | `config/prompts/agent_prompt.md` используется всеми инструментами | файл не читается кодом | `config/prompts/agent_prompt.md`, `config.py:63` |
| MCP-доступ | «read-only» | 3 read + 6 write-инструментов | `README.md`, `SKILL.md`, `agents/mcp_server.py` |
| Статус этапов | Этапы 3 и 5 — ✅ | заглушки | `README.md:207-209`, `core/insights.py`, `core/self_improvement.py` |

---

## 6. План исправлений по приоритетам

### Волна 1 — «чтобы не падало и не врало» (~1 день)
- [ ] **R-01** экранирование FTS-запроса + `try/except OperationalError` (P1, S)
- [ ] **R-04** привести `config/user_profile.example.yaml` к структуре `user: {goal, tasks, topics...}` (P1, S)
- [ ] **R-05** вызывать `ensure_sources_file()` + понятное сообщение при пустом реестре (P1, S)
- [ ] **R-07** `yaml.safe_dump` для frontmatter (P2, S)
- [ ] **R-09** `note_id` без пробела (P2, S)
- [ ] **R-11** ограничить `limit` (P2, S)
- [ ] **R-06** вернуть сниппет содержимого в `kb_search` (P1, S)

### Волна 2 — «чтобы заявленное работало» (~2–3 дня)
- [ ] **R-03** импорт `agent_prompt.md` в `prompt_history` + чтение активной версии (P1, M)
- [ ] **R-02** инсайты: повторяющиеся темы по тегам + вызов `add_link` из конвейера (P1, M)
- [ ] **R-08** транзакционные миграции (P2, M)
- [ ] **R-10** тема от LLM с валидацией по профилю (P2, S–M)
- [ ] **R-27** ретраи/backoff и настраиваемый таймаут LLM (P6, M)

### Волна 3 — гигиена и документация (~1–2 дня)
- [ ] **R-12/R-13/R-14/R-15** единые env-имена, `$GITHUB_ENV`, перевести CI на `kb-agent` или пометить legacy (P3)
- [ ] **R-17/R-18/R-19/R-20/R-21** удалить дубли и мёртвый код, вынести `_output_json` (P4)
- [ ] **R-22…R-25** синхронизировать `docs/*` и `README.md` с кодом, снять «✅» с заглушек (P5)
- [ ] **R-26/R-28/R-29/R-30/R-31/R-32** pydantic-v2 API, мелочи CLI, `run.sh`, `init_stage1.sh` → `legacy/` (P6)

### Definition of done
1. `ruff check .` и `pytest` зелёные (сейчас тестов нет — см. `CONTRIBUTING.md`, раздел «Areas where help is needed»).
2. `pip wheel .` содержит все подпакеты (уже исправлено, F-07).
3. `kb-agent search '"'`/`kb-agent ask` не падают, MCP `kb_search` отвечает без исключений.
4. `agent_prompt` v1 присутствует в `prompt_history`, `self-improve rollback` работает.
5. README/SKILL/docs не содержат утверждений, противоречащих коду.

---

## 7. Приложение А. Команды проверки

```bash
# синтаксис
python -m compileall -q kb_agent scripts

# сборка пакета (после правки pyproject.toml)
python -m pip wheel . --no-deps -w /tmp/wheel && unzip -l /tmp/wheel/*.whl

# FTS-граничные случаи (R-01)
python - <<'PY'
import sys, tempfile, pathlib; sys.path.insert(0, '.')
from kb_agent.storage.state import StateManager
s = StateManager(pathlib.Path(tempfile.mkdtemp())/'db.sqlite'); s.init_db()
s.index_note('A','T','tag','topic','body')
print(s.search('"'))   # sqlite3.OperationalError: unterminated string
PY

# поиск секретов в трекаемых файлах
git grep -n -I -E 'sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{12,}|-----BEGIN (RSA|OPENSSH|PRIVATE)' -- .

# история: остатки ключа (после ротации безопасно, но след остаётся)
git log --oneline --all -S'sk-kimi-whhxin' -- .
```

---

## 8. Приложение Б. Что уже изменено в рабочем дереве

Правки не закоммичены (коммит `c3fe165` содержит только security-удаление `.env.save`).

| Группа | Файлы |
|---|---|
| Код (11) | `agents/mcp_server.py`, `cli/commands/ask.py`, `cli/commands/sources.py`, `config.py`, `core/import_core.py`, `core/insights.py`, `core/relevance.py`, `core/search.py`, `core/self_improvement.py`, `core/update.py`, `feedback/collector.py`, `processing/llm_client.py`, `processing/pipeline.py`, `sources/registry.py`, `sources/telegram.py`, `sources/youtube.py`, `storage/catalog.py`, `storage/pending.py`, `storage/state.py` |
| Сборка | `pyproject.toml` |
| Документация | `README.md`, `SKILL.md`, `.cursorrules`, `docs/architecture.md`, `docs/catalog.md`, `docs/workflow.md`, `docs/Этап 4 — write-операции MCP — план для новой сессии.md`, `План реализации ИИ-агента.md`, `Пожелания к приложению для базы знаний.md`, `config/prompts/agent_prompt.md` |
| Безопасность | `.gitignore`, `.env.example`, `scripts/prepare_stage4_write.sh` |

---

## 9. Приложение В. Закрытая утечка секрета (для истории вопроса)

| Шаг | Статус |
|---|---|
| Ключ `sk-kimi-…` в `.env.save` (коммит `65174af`) | был опубликован в репозитории |
| Ключ отозван в личном кабинете Kimi | ✅ сделано владельцем |
| Значение вырезано из локального `.env.save`, битая первая строка исправлена | ✅ |
| Файл снят с отслеживания (`git rm --cached`) | ✅ коммит `c3fe165` |
| `.env.save` добавлен в `.gitignore`, предупреждение в `.env.example` | ✅ |
| Удаление отправлено в `origin/main` (проверено `git ls-tree origin/main`) | ✅ |
| Ключ остаётся в истории `65174af` | ⚠️ не критично (ключ мёртв); при необходимости — `git filter-repo --invert-paths --path .env.save` + `push --force` |
| Секреты в других трекаемых файлах | ✅ не найдено |

---

*Отчёт подготовлен по результатам статического анализа, сборки пакета и рантайм-проверок на заглушках зависимостей. Пункты, помеченные [проверено], воспроизводились запуском кода.*
