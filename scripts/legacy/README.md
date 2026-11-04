# Legacy-скрипты (не используются приложением)

Эти скрипты — **исторические сборщики первого поколения**. Пакет `kb-agent`
их не вызывает: актуальная реализация находится в `kb_agent/sources/` и работает
с SQLite (`raw_queue`), а не с `_queue.jsonl`.

| Файл | Назначение | Статус |
|---|---|---|
| `collect_telegram.py` | Сбор постов `t.me/s/<channel>` в `_queue.jsonl` | legacy, работает автономно |
| `collect_youtube.py` | Метаданные и субтитры через `yt-dlp` в `_queue.jsonl` | legacy, работает автономно |
| `run.sh` | Запуск обоих сборщиков подряд | legacy |
| `collect_to_vault_incoming.py` | Сбор в `<vault>/Входящие` (жёстко заданные пути) | legacy, требует правки путей |
| `run_to_vault_incoming.sh` | Обёртка для предыдущего | legacy, требует правки путей |
| `init_stage1.sh` | Генератор каркаса Этапа 1 | **не запускать в текущем репозитории**: перезапишет актуальные файлы |

## Обёртки совместимости

В `scripts/` остались тонкие обёртки `collect_telegram.py` и `collect_youtube.py`,
которые просто запускают одноимённые скрипты из `scripts/legacy/`. Они **не
используются** актуальным workflow `.github/workflows/collect.yml` — тот теперь
запускает установленный пакет `kb-agent process run` (SQLite-очередь, а не
`_queue.jsonl`). Обёртки оставлены только для справки и автономного запуска.

Обёртки выставляют `KB_QUEUE_DIR=scripts/`, поэтому `_queue.jsonl`, `_done.txt`,
`_trans_done.txt` и `_transcripts/` создаются в `scripts/`. Без обёрток можно
запускать напрямую:

```bash
KB_QUEUE_DIR=/path/to/queue python3 scripts/legacy/collect_youtube.py list-queue
```

## Состояние workflow

Workflow `.github/workflows/collect.yml` обновлён:

- устанавливает пакет `.[youtube]` и запускает `kb-agent process run`;
- секреты из GitHub Secrets передаются через `$GITHUB_ENV`;
- после прогона сохраняются артефакты `vault` и `state.db`;
- при ошибке конвейера задача завершается с ненулевым exit-кодом.

Актуальные команды приложения: `kb-agent process run`,
`kb-agent sources list|add|remove|enable|disable`.
