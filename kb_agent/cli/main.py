"""Точка входа CLI для kb-agent (Этапы 1-5).

Команды:
  kb-agent --version               — версия
  kb-agent interview init|status|import-core|periodic — профиль и импорт ядра
  kb-agent process run|digest|status                 — конвейер наполнения
  kb-agent ask "<вопрос>"          — ответ со ссылками на заметки
  kb-agent search "<запрос>"       — поиск заметок (список)
  kb-agent insights                — инсайты и связи между заметками
  kb-agent update check            — поиск дублей
  kb-agent sources list|add|remove|enable|disable     — управление источниками
  kb-agent pending                 — список pending-запросов write-операций
  kb-agent confirm <change_id>     — подтвердить и применить pending-запрос
  kb-agent feedback rate|tell|metrics|list           — обратная связь
  kb-agent self-improve [rollback] — метрики и откат промптов
  kb-agent mcp-serve               — MCP-сервер для внешних агентов
"""

from __future__ import annotations

import typer

from kb_agent import __version__
from kb_agent.cli.commands.interview import app as interview_app
from kb_agent.cli.commands.process import (
    app as process_app,
    process_backup,
    process_doctor,
    process_reindex,
    process_retry,
)
from kb_agent.cli.commands.confirm import confirm_command
from kb_agent.cli.commands.pending import pending_command
from kb_agent.cli.commands.feedback import app as feedback_app
from kb_agent.cli.commands.self_improve import app as self_improve_app
from kb_agent.cli.commands.sources import app as sources_app
from kb_agent.cli.commands.ask import (
    ask_command,
    search_command,
    insights_command,
    update_app,
)
from kb_agent.agents.mcp_server import main as mcp_main

app = typer.Typer(
    help="AI-агент для базы знаний ТГК",
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"kb-agent {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        callback=_version_callback,
        is_eager=True,
        help="Вывести версию пакета и выйти",
    ),
) -> None:
    """Корневая команда CLI."""
    pass


# Подключаем группы команд
app.add_typer(interview_app, name="interview", help="Вводное интервью и профиль пользователя")
app.add_typer(process_app, name="process", help="Конвейер наполнения базы знаний")
app.add_typer(feedback_app, name="feedback", help="Обратная связь по заметкам и ответам (Этап 5)")
app.add_typer(
    self_improve_app, name="self-improve", help="Самоулучшение под наблюдением человека (Этап 5)"
)
app.add_typer(sources_app, name="sources", help="Управление источниками данных (Этап 4/5)")

# Подключаем отдельные команды Этапа 4
app.command(name="confirm", help="Подтвердить и применить pending-запрос write-операции")(
    confirm_command
)
app.command(
    name="pending", help="Показать список pending-запросов write-операций (Этап 4)"
)(pending_command)

# Подключаем отдельные команды Этапа 3
app.command(name="ask", help="Задать вопрос базе знаний и получить ответ со ссылками на заметки")(
    ask_command
)
app.command(
    name="search", help="Поиск заметок по запросу (список результатов)"
)(search_command)
app.command(
    name="insights", help="Показать инсайты: повторяющиеся темы, связи между заметками"
)(insights_command)

# Подключаем группу update с подкомандой check (Этап 3)
app.add_typer(update_app, name="update", help="Проверка актуализации базы знаний (Этап 3)")

# Команда MCP-сервера (Этап 4)
@app.command(name="mcp-serve", help="Запустить MCP-сервер для внешних агентов (read-only)")
def mcp_serve() -> None:
    """Запускает MCP-сервер через stdio для доступа внешних агентов."""
    import asyncio

    asyncio.run(mcp_main())


# Команды аудита/восстановления (Волна 1) — доступны как kb-agent <команда>
app.command(name="reindex", help="Перестроить notes_fts из файлов vault (frontmatter)")(
    process_reindex
)
app.command(name="doctor", help="Проверка целостности базы знаний (раздел 9 аудита)")(
    process_doctor
)
app.command(name="retry", help="Повторная обработка элемента очереди")(process_retry)
app.command(name="backup", help="Экспорт SQLite + vault в zip-архив")(process_backup)


if __name__ == "__main__":
    app()
