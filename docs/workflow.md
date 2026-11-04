# Рабочий процесс (Workflow)

## Двухэтапный сбор

| Этап | Кто выполняет | Что делает | Инструмент |
|------|---------------|------------|------------|
| 1 | Скрипт (крон) | Сбор сырых данных в очередь | `collect_telegram.py`, `collect_youtube.py` |
| 2 | Hermes Agent | Анализ через LLM, запись заметок KB | Чат Hermes / ваш LLM |

## Пошаговая инструкция

### Шаг 1. Настройка окружения

```bash
cp .env.example .env
# Отредактируйте .env под свои пути
```

### Шаг 2. Запуск сбора (автоматически по крону или вручную)

```bash
# Telegram
python3 scripts/collect_telegram.py

# YouTube — метаданные в очередь
python3 scripts/collect_youtube.py collect

# YouTube — субтитры для видео в очереди
python3 scripts/collect_youtube.py transcripts
```

### Шаг 3. Просмотр очереди

```bash
python3 scripts/collect_youtube.py list-queue
# или для Telegram — смотрите _queue.jsonl напрямую
```

### Шаг 4. Анализ Hermes Agent

Для каждой записи в очереди:
1. Прочтите текст поста / транскрипцию.
2. Напишите структурированную заметку KB со следующими секциями:
   - `# Заголовок`
   - `## Суть`
   - `## Ключевые идеи`
   - `## Инструменты и ресурсы`
   - `## Практические выводы`
   - `**Дата:**`
   - `**Канал:**`
   - `**Источник:**`
   - `#теги`
3. Добавьте YAML frontmatter (пример в `docs/catalog.md`).

### Шаг 5. Пометка обработанной записи

```bash
python3 scripts/collect_youtube.py done <VIDEO_ID>
# Для Telegram — аналогично, пометьте в _done.txt
```

### Шаг 6. Обновление каталога

```bash
python3 scripts/collect_telegram.py catalog
# пересобирает Каталог.md из всех существующих заметок
```

## Пример итоговой заметки (YouTube)

```markdown
---
source: youtube
channel: vladimir_kkkkkk
url: https://www.youtube.com/watch?v=XXXXXXX
thumbnail: 'https://i.ytimg.com/vi/XXXXXXX/maxresdefault.jpg'
date_saved: 2026-10-22
duration: 37:03
tags: [youtube, GPT-6, Astra, тестирование-моделей]
transcript: _transcripts/XXXXXXX.txt
---

# GPT-6 Astra. Проверка на РЕЛЬНЫХ задачах

## Суть
...

## Ключевые идеи
- ...

## Инструменты и ресурсы
- ...

## Практические выводы
- ...

**Дата:** 2026-09-10T00:00:00+00:00
**Канал:** vladimir_kkkkkk
**Источник:** [https://www.youtube.com/watch?v=XXXXXXX](...)

#видео #youtube #GPT-6 #Astra
```

## Пример итоговой заметки (Telegram)

```markdown
# Агенты OpenAI ослабили контроль за собой

## Суть
OpenAI опубликовала отчёт о двух случаях...

## Ключевые идеи
- ...

## Инструменты и ресурсы
- ...

## Практические выводы
- ...

**Дата:** 2026-10-21T10:09:58+00:00
**Канал:** ragnarokzone
**Источник:** [https://t.me/ragnarokzone/264](...)

#телеграмм #ragnarokzone #OpenAI #безопасность
```

## Что НЕ входит в этот репозиторий

- Сами заметки KB (они находятся в вашем личном Obsidian vault).
- `_queue.jsonl`, `_done.txt`, `_transcripts/` — рабочие файлы сбора, не для публикации.
- Логи cron.
