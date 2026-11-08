# Участие в развитии kb-agent

Мы приветствуем вклад в проект! Это локальный ИИ-агент для персональной базы знаний. Вот как вы можете помочь.

## 🛠️ Как участвовать

### 1. Форк и клонирование

```bash
git clone https://github.com/DenisZakharov2/personal-kb-agent.git
cd personal-kb-agent
```

### 2. Настройка окружения разработки

```bash
# Создать виртуальное окружение
python3 -m venv .venv
source .venv/bin/activate

# Установить пакет в режиме разработки с dev-зависимостями
pip install -e ".[dev]"

# Установить pre-commit хуки (опционально)
pip install ruff
ruff check .
```

### 3. Создание ветки

```bash
git checkout -b feature/ваша-фича
# или
git checkout -b fix/ваш-фикс
```

### 4. Внесение изменений и тестирование

```bash
# Линтинг
ruff check .

# Типизация
mypy kb_agent

# Тесты
pytest -v

# Проверка CLI
kb-agent --help
kb-agent sources list
```

### 5. Коммит и пуш

Используем [Conventional Commits](https://www.conventionalcommits.org/ru/v1.0.0/):

```bash
git add .
git commit -m "feat: описание вашей фичи"
# или
git commit -m "fix: описание исправления"
git push origin feature/ваша-фича
```

### 6. Pull Request

Откройте PR с описанием:
- Что изменено и почему
- Скриншоты или вывод CLI (если применимо)
- Связанные Issue (если есть)

---

## 📁 Структура проекта

```
personal-kb-agent/
├── README.md              # Документация проекта
├── LICENSE                # MIT лицензия
├── pyproject.toml         # Метаданные и зависимости
├── .env.example           # Шаблон переменных окружения
├── .gitignore             # Исключения из git
├── SECURITY.md            # Политика безопасности
├── CHANGELOG.md           # История изменений
├── kb_agent/              # Основной пакет
│   ├── cli/               # CLI команды (Typer)
│   │   ├── commands/
│   │   │   ├── ask.py           # ask, search, insights, update check
│   │   │   ├── feedback.py      # feedback rate/tell/metrics/list
│   │   │   ├── interview.py     # interview init/status/periodic
│   │   │   ├── pending.py       # pending confirm/list
│   │   │   ├── process.py       # process run/digest/status
│   │   │   ├── self_improve.py  # self-improve rollback
│   │   │   └── sources.py       # sources add/remove/enable/disable/list
│   │   ├── output.py            # Общий JSON output helper
│   │   └── main.py              # Точка входа CLI
│   ├── core/                # Ядро логики
│   │   ├── interview.py         # InterviewManager
│   │   ├── insights.py          # InsightsEngine (темы, пробелы, связи)
│   │   ├── prompts.py           # Версионированные системные промпты
│   │   ├── relevance.py         # Relevance Gate
│   │   ├── search.py            # FTS5 поиск + LLM ответ
│   │   ├── self_improvement.py  # SelfImprovementEngine
│   │   └── update.py            # KnowledgeUpdater
│   ├── processing/          # Конвейер обработки
│   │   ├── llm_client.py        # LLM клиент (OpenAI-совместимый, ретраи)
│   │   └── pipeline.py          # Pipeline (сбор → relevance → LLM → vault)
│   ├── sources/             # Источники данных
│   │   ├── base.py              # BaseSource абстрактный класс
│   │   ├── registry.py          # SourceRegistry (sources.yaml)
│   │   ├── manager.py           # SourceManager (сбор всех/одного)
│   │   ├── telegram.py          # Telegram коллектор
│   │   └── youtube.py           # YouTube коллектор
│   ├── storage/             # Слой хранения
│   │   ├── state.py             # StateManager (SQLite)
│   │   ├── vault.py             # VaultManager (Obsidian vault)
│   │   ├── catalog.py           # CatalogGenerator (Каталог.md)
│   │   └── pending.py           # PendingManager (pending изменения)
│   ├── agents/              # Интеграция с внешними агентами
│   │   └── mcp_server.py        # MCP сервер (kb-agent mcp-serve)
│   ├── feedback/            # Сбор обратной связи
│   │   ├── collector.py         # FeedbackCollector
│   │   └── metrics.py           # FeedbackMetricsCollector
│   ├── config.py            # Конфигурация приложения
│   ├── utils/
│   │   └── logger.py            # Настройка логирования
│   └── __init__.py
├── config/                  # Шаблоны конфигурации (dev fallback)
│   ├── sources.example.yaml
│   ├── user_profile.example.yaml
│   └── prompts/
│       ├── agent_prompt.md
│       ├── answer_prompt.md
│       └── summarize_prompt.md
├── kb_agent/resources/      # Ресурсы, упакованные в wheel
│   ├── prompts/
│   │   ├── agent_prompt.md
│   │   ├── answer_prompt.md
│   │   └── summarize_prompt.md
│   └── config/
│       ├── sources.example.yaml
│       └── user_profile.example.yaml
├── scripts/                 # Утилиты
│   ├── prepare_stage4_write.sh
│   └── legacy/              # Legacy коллекторы (_queue.jsonl), не используются
├── tests/                   # Тесты (297 тестов)
├── docs/                    # Документация
│   ├── architecture.md
│   ├── workflow.md
│   └── catalog.md
└── SKILL.md                 # MCP skill для ИИ-агентов
```

---

## 🎯 Где нужна помощь

- **Новые источники**: Реализуйте `BaseSource` для веб-сайтов, RSS, arXiv, личных файлов
- **Тесты**: Добавьте pytest покрытие для pipeline, relevance gate, storage
- **Документация**: Добавьте больше примеров, переведите на английский
- **CI/CD**: Улучшите GitHub Actions (уже настроен базовый)
- **Публикация**: Помогите с публикацией в PyPI (trusted publishing)
- **Локализация**: Переведите README и docs на английский

---

## 📝 Конвенции коммитов

Используйте Conventional Commits:

- `feat:` — новая фича
- `fix:` — багфикс
- `docs:` — документация
- `refactor:` — рефакторинг
- `test:` — тесты
- `chore:` — обслуживание
- `security:` — безопасность
- `perf:` — производительность

Пример: `feat: add RSS source with article extraction`

---

## 🧪 Написание тестов

- Тесты кладутся в `tests/` с префиксом `test_`
- Используйте `pytest` и фикстуры из `conftest.py`
- Для изоляции используйте временные директории (`tmp_path`)
- Мокайте внешние вызовы (LLM, HTTP, yt-dlp)

Пример:
```python
def test_relevance_gate_filters_spam():
    gate = RelevanceGate({"topics": ["AI"], "relevance_threshold": 0.3})
    result = gate.check("Розыгрыш iPhone 15!", "Спам")
    assert result.relevant is False
    assert "спам" in result.reason.lower()
```

---

## 📄 Лицензия

Участвуя, вы соглашаетесь, что ваши вклады будут лицензированы под MIT License.

---

## ❓ Вопросы?

Откройте [Issue](https://github.com/DenisZakharov2/personal-kb-agent/issues) или начните [Discussion](https://github.com/DenisZakharov2/personal-kb-agent/discussions).