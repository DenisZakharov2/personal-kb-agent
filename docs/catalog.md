# Каталог базы знаний (пример структуры)

Каталог `Каталог.md` пересобирается автоматически из всех существующих заметок в папках `Телеграмм/` и `YouTube/` внутри Obsidian vault.

## Пример записи в каталоге

```markdown
## 🤖 Claude Code, Codex и ИИ для разработки

- [[Телеграмм/2026-10-21T10-09_ragnarokzone_264.md]] — 2026-10-21, источник: [ragnarokzone](https://t.me/s/ragnarokzone)
  > OpenAI опубликовала отчёт о двух случаях, когда ИИ-агенты ослабили систему слежения за собой.

- [[YouTube/20260910_GPT-6_Astra_Проверка_на_РЕЛЬНЫХ_задачах_4DA91qlTUVU.md]] — 2026-09-10, источник: [vladimir_kkkkkk](https://www.youtube.com/@vladimir_kkkkkk)
  > Практическая проверка модели GPT-6 Astra на реальных задачах: автомонтаж, лендинг, чат-бот.
```

## YAML frontmatter заметки

Каждая итоговая заметка KB начинается с frontmatter:

```yaml
---
source: youtube          # или telegram
channel: vladimir_kkkkkk
url: https://www.youtube.com/watch?v=XXXX
thumbnail: 'https://i.ytimg.com/vi/XXXX/maxresdefault.jpg'
date_saved: 2026-10-22
duration: 37:03
tags: [youtube, GPT-6, Astra]
transcript: _transcripts/XXXX.txt
---
```

## Категории каталога

Каталог Gruppiert записи по разделам:
- 🤖 Claude Code, Codex и ИИ для разработки
- 🔌 n8n и автоматизация процессов
- 💎 Бесплатный доступ к LLM и API
- 🧠 ИИ-агенты (автономные agents)
- 📰 Новости ИИ и технологий
- 🛠 Инструменты и сервисы для работы с ИИ
- 🎬 Мультимедиа: видео, 3D, голос, дизайн
- 🤖 Роботы и физическая автоматизация
- 🔒 Безопасность, приватность, блокировки
- 💻 Программирование и веб-разработка
- 📎 Прочее
