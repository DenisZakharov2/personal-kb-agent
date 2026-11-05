---
name: kb-agent
description: "Локальный ИИ-агент для базы знаний в Obsidian. Собирает материалы из Telegram и YouTube, суммаризирует через LLM, предоставляет поиск через FTS5 и MCP-доступ."
version: "0.2.0"
---

# Навык kb-agent для внешних ИИ-агентов (Этап 4)

Этот файл позволяет Hermes, Claude Code, OpenClaw, Kimi Claw и другим агентам
подключаться к базе знаний ТГК через MCP-сервер kb-agent.

## Описание
kb-agent — это локальный ИИ-агент для базы знаний в Obsidian. Он собирает материалы
из Telegram-каналов и YouTube, суммаризирует их через LLM (OpenAI, Kimi и др.),
сохраняет заметки с YAML frontmatter и предоставляет поиск через FTS5.

## Доступ через MCP
MCP-сервер запускается командой:
```
kb-agent mcp-serve
```

## Доступные инструменты

### Read-only (доступны сразу)

**Инструменты чтения:** `kb_search`, `kb_get_note`, `kb_get_insights`, `kb_sources_status`, `kb_list_proposals`, `kb_get_proposal`.

#### kb_search
Поиск по заметкам базы знаний через FTS5 с пагинацией.
Параметры:
- `query` (строка, обязательный) — поисковый запрос
- `limit` (целое, 1-20, по умолчанию 5) — максимум результатов на страницу
- `cursor` (целое, по умолчанию 0) — смещение для пагинации

Пример:
```json
{"query": "ИИ и автоматизация", "limit": 5, "cursor": 0}
```

#### kb_get_note
Получение содержимого заметки по её ID.
Параметры:
- `note_id` (строка, обязательный) — идентификатор заметки

Пример:
```json
{"note_id": "2026-11-03T20_8f49ee32"}
```

#### kb_get_insights
Получение инсайтов базы знаний (связи между заметками).
Параметры: нет

#### kb_sources_status
Получение состояния всех источников: список, тип, URL, флаг включён/выключен, время последней проверки.

#### kb_list_proposals
Список предложений по улучшению (self-improve) с фильтром по статусу.
Параметры:
- `status` (строка, необязательный) — фильтр: proposed / confirmed / applied / rejected / failed

#### kb_get_proposal
Детали одного предложения по улучшению по proposal_id.
Параметры:
- `proposal_id` (строка, обязательный) — идентификатор предложения

### Write-инструменты (требуют подтверждения человеком)

Каждый вызов создаёт pending-запрос и возвращает `change_id`. Изменение применяется только
после явного человеческого подтверждения через `kb-agent confirm <change_id>`.

- `kb_add_source` — `source_type` (`telegram` | `youtube`), `channel`, `enabled`
- `kb_remove_source` — `source_id`
- `kb_enable_source` — `source_id`, `enabled`
- `kb_update_profile` — `updates` (объект с полями профиля)
- `kb_revert_prompt` — `prompt_name`, `version` (необязательно)
- `kb_reject_proposal` — `proposal_id`, `reason` (необязательно)

## Пример использования (Cursor / Claude Code)
Добавьте в `.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "kb-agent": {
      "command": "kb-agent",
      "args": ["mcp-serve"]
    }
  }
}
```

## Ограничения
- Read-only инструменты работают сразу, write-инструменты — только через pending-подтверждение
- Изменения профиля и источников применяются после `kb-agent confirm <change_id>` (через CLI, модель не может подтвердить изменение самостоятельно)
- Веб-интерфейс не используется

## Репозиторий
https://github.com/DenisZakharov2/personal-kb-agent
