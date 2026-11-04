# Архитектура kb-agent

> Актуальное описание. Сборщики первого поколения (`_queue.jsonl`, ручной анализ через
> Hermes Agent) вынесены в `scripts/legacy/` и приложением **не используются**.

## Общая схема

```
Источники: Telegram (t.me/s), YouTube (yt-dlp)
        │  kb_agent/sources/*  (SourceManager + collect_*_source)
        ▼
   raw_queue  (SQLite, data/state.db)
        │  Pipeline: Relevance Gate → LLM-суммаризация → теги/тема/связи
        ▼
   vault (тематические папки, YAML frontmatter) + notes_fts + note_links
        │  CatalogGenerator
        ▼
   Каталог.md, Дайджест.md
```

## Слои

| Слой | Модули | Ответственность |
|---|---|---|
| Конфигурация | `kb_agent/config.py` | `.env` + переменные окружения + пути (Pydantic v2) |
| Источники | `sources/registry.py`, `sources/manager.py`, `sources/telegram.py`, `sources/youtube.py` | SQLite-таблица `sources` как источник истины, YAML — только import/export, сбор в `raw_queue` |
| Обработка | `processing/pipeline.py`, `processing/llm_client.py`, `core/relevance.py`, `core/prompts.py` | Relevance Gate, суммаризация, теги и тема, связи между заметками, версии промптов |
| Хранение | `storage/state.py`, `storage/vault.py`, `storage/catalog.py`, `storage/pending.py` | состояние (SQLite), заметки (.md), каталог, pending-запросы |
| Поиск и инсайты | `core/search.py`, `core/insights.py`, `core/update.py` | FTS5-поиск, LLM-ответ, инсайты, поиск дублей |
| Интеграция | `agents/mcp_server.py`, `cli/*` | MCP-сервер (stdio), CLI (Typer) |
| Обратная связь | `feedback/*`, `core/self_improvement.py` | оценки, метрики, предложения по промптам |

## Хранение данных

- **Состояние** — SQLite `data/state.db`: `raw_queue`, `notes_fts` (FTS5), `note_links`, `feedback`, `prompt_history`, `processed`, `pending_changes`, `sources`.
- **Конфигурация источников** — SQLite-таблица `sources` в `data/state.db` (каноническое хранилище). `data/sources.yaml` используется только для импорта/экспорта; создаётся при первом запуске из `KB_TELEGRAM_CHANNELS` / `KB_YOUTUBE_CHANNELS`, иначе из `config/sources.example.yaml`.
- **Профиль** — `data/user_profile.yaml` (структура: всё внутри секции `user`).
- **Заметки** — Markdown с YAML frontmatter в тематических папках vault.
- **Промпты** — `config/prompts/agent_prompt.md`; при первом запуске импортируется в `prompt_history` как версия 1, поэтому работают откат и self-improve.

## LLM

Суммаризацию и ответы выполняет внешний LLM через OpenAI-совместимый API
(`LLMClient`, httpx, повторы при 429/5xx, настраиваемый таймаут). Локальная LLM не требуется.
