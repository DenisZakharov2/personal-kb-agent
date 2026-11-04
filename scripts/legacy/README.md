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

Актуальные команды: `kb-agent process run`, `kb-agent sources list|add|remove|enable|disable`.
