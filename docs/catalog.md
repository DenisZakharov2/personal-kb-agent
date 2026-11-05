# Каталог базы знаний

Файл `Каталог.md` пересобирается автоматически (`CatalogGenerator`) после каждого
`kb-agent process run` и группирует заметки по полю `topic` из YAML frontmatter.
Служебные файлы (`Каталог.md`, `Дайджест.md`) в каталог не попадают.

## Пример записи в каталоге

```markdown
# Каталог базы знаний

Всего заметок: 12

## По темам

### ИИ и нейросети

- [Проверка GPT-6 Astra на реальных задачах](ИИ_и_нейросети/2026-09-10_проверка_gpt-6_astra.md) — 2026-09-10
```

## YAML frontmatter заметки

Заметку пишет `VaultManager.write_note` через `yaml.safe_dump`, поэтому значения
экранируются корректно (заголовок с `: ` не ломает YAML).

```yaml
---
source: youtube
source_id: yt_example_channel
external_id: yt_4DA91qlTUVU
url: https://www.youtube.com/watch?v=4DA91qlTUVU
date_saved: 2026-10-22
published_at: 2026-09-10
received_at: '2026-10-22 10:15:00'
note_id: 2026-10-22T10_8f49ee32
previous_note_id: null
frontmatter_schema_version: 1
title: Проверка GPT-6 Astra на реальных задачах
tags:
- youtube
- GPT-6
- Astra
topic: ИИ и нейросети
---
```

Поля `title`, `tags`, `topic` используются каталогом, поиском (FTS5) и инсайтами;
`note_id` — идентификатор для `kb_get_note`, `kb-agent feedback rate` и связей.
`topic` определяется LLM при суммаризации и выбирается из списка тем профиля
(`data/user_profile.yaml`). Если тема не определена или не совпадает ни с одной
темой профиля, применяется «Общее».
