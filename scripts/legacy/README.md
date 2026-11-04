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
которые просто запускают одноимённые скрипты из `scripts/legacy/`. Они нужны,
чтобы существующий workflow `.github/workflows/collect.yml` (в нём прописаны
именно эти пути) продолжал работать.

Обёртки выставляют `KB_QUEUE_DIR=scripts/`, поэтому `_queue.jsonl`, `_done.txt`,
`_trans_done.txt` и `_transcripts/` создаются в `scripts/` — ровно там, где их
забирает шаг `upload-artifact`. Без обёрток можно запускать напрямую:

```bash
KB_QUEUE_DIR=/path/to/queue python3 scripts/legacy/collect_youtube.py list-queue
```

## Известное ограничение workflow

Workflow **не обновлён** (в нём остались `KB Collect`, `cp .env.example .env`
и переменная `KB_YT_CHANNEL_URL`): у токена, которым выполняется push из этой
среды, нет права `workflows`, GitHub отклоняет такие коммиты.

Следствие: переменные из `secrets` попадают в `.env`, который скрипты не читают
(dotenv не используется), поэтому сбор идёт на значениях по умолчанию.

Готовый обновлённый workflow (переменные через `$GITHUB_ENV`, пути `scripts/legacy`,
имя «Legacy queue collector») лежит в артефакте `collect-workflow.patch`;
применить можно через веб-интерфейс GitHub (Edit → вставить содержимое) или
на машине с правом `workflow`:

```bash
git am collect-workflow.patch && git push origin main
```

Актуальные команды приложения: `kb-agent process run`,
`kb-agent sources list|add|remove|enable|disable`.
