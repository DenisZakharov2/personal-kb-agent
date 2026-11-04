# Рабочий процесс (Workflow)

## Быстрый цикл

```
pip install -e .                 # установка пакета и команды kb-agent
kb-agent interview init          # профиль (цели, темы, источники)
kb-agent sources add telegram https://t.me/s/<канал>
kb-agent process run             # сбор → relevance → LLM → vault → каталог/дайджест
kb-agent ask "<вопрос>"           # ответ со ссылками на заметки
```

## Пошагово

### 1. Настройка окружения

```bash
cp .env.example .env
# В .env: KB_VAULT_PATH, KB_OPENAI_API_KEY, KB_TELEGRAM_CHANNELS, KB_YOUTUBE_CHANNELS
```

### 2. Профиль пользователя

```bash
kb-agent interview init      # создаст data/user_profile.yaml
kb-agent interview status    # статус профиля и БД
kb-agent interview periodic  # ежемесячное обновление целей и тем
```

### 3. Источники

```bash
kb-agent sources list
kb-agent sources add telegram https://t.me/s/ragnarokzone
kb-agent sources add youtube https://youtube.com/@channel
kb-agent sources enable tg_ragnarokzone
kb-agent sources disable yt_channel
kb-agent sources remove tg_ragnarokzone
```

Файл `data/sources.yaml` создаётся автоматически при первом запуске сбора.
Изменения из MCP-инструментов проходят через `pending` → `kb-agent confirm <id>`.

### 4. Наполнение базы знаний

```bash
kb-agent process run       # полный цикл
kb-agent process digest    # тот же цикл + Markdown-дайджест в stdout
kb-agent process status    # статистика очереди и таблиц
```

Что происходит внутри:
1. **Сбор** — `SourceManager` опрашивает включённые источники, новые материалы попадают в `raw_queue`.
2. **Relevance Gate** — отсев спама и нерелевантного по `user_profile.yaml`; пропуски помечаются `skipped` с причиной.
3. **Суммаризация** — LLM строит заметку (суть, идеи, инструменты, выводы, теги, тема).
4. **Запись** — заметка с YAML frontmatter попадает в тематическую папку vault (`VaultManager`).
5. **Индексация** — `notes_fts` (FTS5) и связи `note_links` по общим тегам.
6. **Каталог и дайджест** — `Каталог.md` и `Дайджест.md` пересобираются автоматически.

### 5. Работа с базой

```bash
kb-agent search "автоматизация n8n"   # список совпадений (FTS5, без LLM)
kb-agent ask "что я накопил про ИИ-агентов?"  # LLM-ответ со ссылками
kb-agent insights                     # повторяющиеся темы, пробелы, связи
kb-agent update check                 # поиск возможных дублей
```

### 6. Обратная связь и самоулучшение

```bash
kb-agent feedback rate <note_id> 5 --comment "хорошая суммаризация"
kb-agent feedback tell "нужно больше примеров кода"
kb-agent feedback metrics
kb-agent self-improve                       # метрики обратной связи
kb-agent self-improve rollback agent_prompt 1   # откат промпта к версии
```

### 7. Доступ для внешних агентов

```bash
kb-agent mcp-serve    # MCP-сервер (stdio) для Cursor/Claude Code/Hermes и др.
```

Инструменты: `kb_search`, `kb_get_note`, `kb_get_insights` (read-only) и
write-инструменты через pending-подтверждение. Подробности — `SKILL.md`.

## Legacy-сборщики

Скрипты первого поколения (`_queue.jsonl`, без SQLite) лежат в `scripts/legacy/`
и приложением не используются — см. `scripts/legacy/README.md`.
