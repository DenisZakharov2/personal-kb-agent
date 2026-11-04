# Этап 4 — write-операции MCP: план для новой сессии

Проект: База знаний (personal-kb-agent)  
Репозиторий: https://github.com/DenisZakharov2  
Сессия: реализация write-операций в MCP-сервере (добавление/изменение/удаление источников и профиля с подтверждением человека)

## Что уже готово (Этапы 0–4)

- `kb-agent mcp-serve` — MCP-сервер, read-only: `kb_search`, `kb_get_note`, `kb_get_insights`
- CLI: `ask`, `search`, `insights`, `update check`, `interview init/status/import-core`, `process run/status`
- `config/prompts/agent_prompt.md` — универсальный системный промпт
- `SKILL.md`, `.cursor/mcp.json`, `.cursorrules` — файлы интеграции

## Что нужно реализовать в новой сессии

### 1. Новые MCP-инструменты (write, требуют подтверждения человека)

| Инструмент | Описание | Требует подтверждения |
|------------|----------|----------------------|
| `kb_add_source` | Добавить источник в `sources.yaml` | Да |
| `kb_remove_source` | Удалить источник из `sources.yaml` | Да |
| `kb_enable_source` | Включить/выключить источник (`enabled`) | Да |
| `kb_update_profile` | Обновить `user_profile.yaml` (цели, темы, частота) | Да |
| `kb_revert_prompt` | Откат версии промпта в `prompt_history` | Да |

### 2. Модель подтверждения человека

Каждый write-инструмент должен:

1. **Вернуть предложение, а не выполнить его** — инструмент возвращает `pending_confirmation: true` с описанием изменения и запросом на подтверждение
2. **Хранить pending-запрос** — во временном состоянии (файл или SQLite-таблица `pending_changes`) до подтверждения
3. **Требует отдельного вызова `kb_confirm_change`** с идентификатором pending-запроса
4. **Логировать все изменения** в `prompt_history` или отдельной таблице `change_log`

Схема pending-запроса:
- `id` — уникальный идентификатор
- `type` — `add_source` / `remove_source` / `enable_source` / `update_profile` / `revert_prompt`
- `payload` — JSON с параметрами изменения
- `created_at` — время создания
- `expires_at` — время истечения (например, через 10 минут)
- `confirmed` — булево, по умолчанию false

### 3. Файлы для создания

| Файл | Содержимое |
|------|------------|
| `kb_agent/cli/commands/mcp_write.py` | Команды CLI для подтверждения изменений (`kb-agent confirm <id>`) |
| `kb_agent/storage/pending.py` | Менеджер pending-запросов (SQLite-таблица `pending_changes`) |
| `kb_agent/agents/mcp_server.py` (дополнение) | Новые write-инструменты + логика подтверждения |
| `docs/Этап 4 — write-операции MCP.md` | Документация по новым инструментам |

### 4. Пример потока работы

```
Агент вызывает kb_add_source — MCP возвращает pending-запрос с ID и описанием
Агент запрашивает у человека подтверждение — Человек говорит "добавь"
Агент вызывает kb_confirm_change <ID> — изменение применяется в sources.yaml
MCP возвращает успех — Человек видит обновлённый список источников
```

### 5. SQLite-таблица pending_changes

```sql
CREATE TABLE IF NOT EXISTS pending_changes (
    id TEXT PRIMARY KEY,
    change_type TEXT NOT NULL CHECK (change_type IN ('add_source','remove_source','enable_source','update_profile','revert_prompt')),
    payload TEXT NOT NULL,  -- JSON
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    confirmed INTEGER NOT NULL DEFAULT 0,
    confirmed_at TEXT
);
```

### 6. Проверка на новую сессию

- [ ] `kb-agent mcp-serve` запускается и предоставляет write-инструменты
- [ ] `kb_add_source` возвращает pending-запрос вместо немедленного изменения
- [ ] `kb-agent confirm <id>` применяет изменение в `sources.yaml`
- [ ] Изменения валидируются (источник проверяется на доступность перед добавлением)
- [ ] Все изменения логируются (история, кто и когда изменил)
- [ ] Pending-запросы истекают по времени (очистка stale записей)

### 7. Что НЕ делать

- Не выполнять write-операцию без явного подтверждения человека
- Не изменять `sources.yaml` напрямую из MCP без pending-потока
- Не добавлять новые библиотеки без обсуждения
- Не делать write-операции доступными без аутентификации/подтверждения

---

После реализации этой сессии Etap 4 будет полностью завершён: read-only + write с подтверждением человека.
