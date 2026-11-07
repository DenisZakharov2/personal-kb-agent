"""Команды поиска, инсайтов и актуализации (Этап 3).

Экспортирует функции и группу для регистрации на главном приложении CLI:

  kb-agent ask "<вопрос>"           — ответ со ссылками на заметки
  kb-agent search "<запрос>"        — поиск заметок (список)
  kb-agent insights                 — инсайты и связи
  kb-agent update check             — поиск дублей и слияние
"""

from __future__ import annotations

import typer

from kb_agent.cli.output import output_json as _output_json
from kb_agent.config import load_config
from kb_agent.core.insights import InsightsEngine
from kb_agent.core.search import SearchEngine, SearchResult, format_search_response
from kb_agent.core.update import KnowledgeUpdater
from kb_agent.processing.llm_client import LLMClient
from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.commands.ask")


def ask_command(
    question: str,
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Задать вопрос базе знаний и получить ответ со ссылками на заметки."""
    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        llm = LLMClient(config)
        engine = SearchEngine(config, state, llm)
        response = engine.search(question)

        if json_output:
            _output_json(
                {
                    "question": question,
                    "answer": response.answer or "В базе недостаточно данных.",
                    "enough_data": response.enough_data,
                    "sources": response.sources,
                    "results": [
                        {
                            "note_id": r.note_id,
                            "title": r.title,
                            "tags": r.tags,
                            "topic": r.topic,
                            "content": r.content,
                        }
                        for r in response.results
                    ],
                }
            )
        else:
            typer.echo(format_search_response(question, response))
    finally:
        state.close()


def search_command(
    query: str,
    limit: int = typer.Option(5, "--limit", "-l", help="Максимум результатов"),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Поиск заметок по запросу (без LLM-синтеза, только список)."""
    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        results = [
            SearchResult(
                note_id=row["note_id"],
                title=row["title"] or "Без названия",
                tags=row["tags"] or "",
                topic=row["topic"] or "",
                content=row.get("content", "") or "",
            )
            for row in state.search(query, limit=limit)
        ]

        if json_output:
            _output_json(
                {
                    "query": query,
                    "found": len(results),
                    "results": [
                        {
                            "note_id": r.note_id,
                            "title": r.title,
                            "tags": r.tags,
                            "topic": r.topic,
                            "content": r.content,
                        }
                        for r in results
                    ],
                }
            )
        else:
            typer.echo(f"## Результаты поиска: {query}")
            typer.echo(f"Найдено: {len(results)}")
            typer.echo("")
            for r in results:
                typer.echo(f"- **{r.title}** (ID: {r.note_id}, теги: {r.tags}, тема: {r.topic})")
    finally:
        state.close()


def insights_command(
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Показать инсайты: повторяющиеся темы, связи между заметками."""
    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        engine = InsightsEngine(config, state)
        response = engine.get_insights()

        if json_output:
            _output_json(
                {
                    "insights": [
                        {
                            "statement": i.statement,
                            "link_type": i.link_type,
                            "confidence": i.confidence,
                            "evidence_note_ids": i.evidence_note_ids,
                            "benefit": i.benefit,
                        }
                        for i in response.insights
                    ]
                }
            )
        else:
            if not response.insights:
                typer.echo("Инсайтов пока нет. Добавьте больше заметок для анализа.")
                return
            typer.echo("## Инсайты базы знаний")
            typer.echo("")
            for i, insight in enumerate(response.insights, 1):
                typer.echo(f"{i}. {insight.statement}")
                typer.echo(f"   Тип: {insight.link_type}, уверенность: {insight.confidence}")
                typer.echo(f"   Доказательства: {', '.join(insight.evidence_note_ids)}")
                typer.echo(f"   Польза: {insight.benefit}")
                typer.echo("")
    finally:
        state.close()


# Группа команд update (для kb-agent update check)
update_app = typer.Typer(help="Проверка актуализации базы знаний (Этап 3)")


@update_app.command(name="check")
def update_check_command(
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Поиск дублей и предложение слияния заметок."""
    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        updater = KnowledgeUpdater(config, state)
        report = updater.check_duplicates()

        if json_output:
            _output_json(
                {
                    "duplicates": [
                        {
                            "note_id_1": d.note_id_1,
                            "title_1": d.title_1,
                            "note_id_2": d.note_id_2,
                            "title_2": d.title_2,
                            "similarity_reason": d.similarity_reason,
                        }
                        for d in report.duplicates
                    ],
                    "conflicts": report.conflicts,
                    "suggestions": report.suggestions,
                }
            )
        else:
            typer.echo("## Проверка актуализации базы знаний")
            typer.echo("")
            if report.duplicates:
                typer.echo(f"Найдено возможных дублей: {len(report.duplicates)}")
                for d in report.duplicates:
                    typer.echo(
                        f"- {d.note_id_1} '{d.title_1}' <-> {d.note_id_2} "
                        f"'{d.title_2}' ({d.similarity_reason})"
                    )
            else:
                typer.echo("Дубликатов не найдены.")
            typer.echo("")
            for s in report.suggestions:
                typer.echo(f"- {s}")
    finally:
        state.close()
