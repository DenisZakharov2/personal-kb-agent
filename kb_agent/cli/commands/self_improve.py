"""Команды самоулучшения (Этап 5).

kb-agent self-improve                                 — показать метрики и предложения
kb-agent self-improve list                            — список предложений по улучшению
kb-agent self-improve propose-prompt <имя> <причина>  — предложить изменение промпта
kb-agent self-improve apply-prompt <id>               — применить предложение промпта
kb-agent self-improve reject <id>                     — отклонить предложение
kb-agent self-improve propose-config <ключ> <старое> <новое> <причина>
                                                        — предложить изменение конфига
kb-agent self-improve apply-config <id> <новое>       — применить предложение конфига
kb-agent self-improve rollback <имя> <версия>         — откатить промпт к версии
"""

from __future__ import annotations

import typer

from kb_agent.cli.output import output_json as _output_json
from kb_agent.config import load_config
from kb_agent.core.self_improvement import ImprovementProposal, SelfImprovementEngine
from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger

app = typer.Typer(
    help="Самоулучшение базы знаний под наблюдением человека (Этап 5)",
    invoke_without_command=True,
)
logger = setup_logger("kb_agent.cli.self_improve")


def _read_content(content_arg: str, content_file: str) -> str:
    """Вернуть содержимое: из аргумента или из файла (если указан --content-file)."""
    if content_file:
        from pathlib import Path

        return Path(content_file).read_text(encoding="utf-8")
    return content_arg


@app.callback()
def self_improve(
    ctx: typer.Context,
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Показать метрики и предложения по улучшению промптов и конфигурации."""
    if ctx.invoked_subcommand is not None:
        return
    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        engine = SelfImprovementEngine(state)
        metrics = engine.collect_feedback_metrics()

        if json_output:
            _output_json(
                {
                    "total_ratings": metrics["total_ratings"],
                    "average_score": metrics["average_score"],
                    "score_distribution": metrics["score_distribution"],
                    "total_messages": metrics["total_messages"],
                    "recent_feedback": metrics["recent_feedback"],
                }
            )
            return

        typer.echo("## Самоулучшение базы знаний")
        typer.echo("")
        typer.echo("### Метрики обратной связи")
        typer.echo(f"Всего оценок: {metrics['total_ratings']}")
        if metrics["average_score"] is not None:
            typer.echo(f"Средняя оценка: {metrics['average_score']}")
            typer.echo("Распределение:")
            for score in sorted(metrics["score_distribution"]):
                count = metrics["score_distribution"][score]
                typer.echo(f"  {score}: {count}")
        else:
            typer.echo("Оценок пока нет.")
        typer.echo(f"Сообщений: {metrics['total_messages']}")
        typer.echo("")

        typer.echo("### Предложения по улучшению")
        typer.echo(
            "Пока нет автоматических предложений. "
            "Используйте 'kb-agent feedback rate/tell' для сбора обратной связи, "
            "затем предложения будут сформированы на основе метрик."
        )
        typer.echo("")
        typer.echo(
            "Доступные действия:\n"
            "  kb-agent self-improve list                                  — список предложений\n"
            "  kb-agent self-improve propose-prompt <имя> <причина>        — предложить изменение промпта\n"
            "  kb-agent self-improve apply-prompt <id>                     — применить предложение промпта\n"
            "  kb-agent self-improve reject <id>                           — отклонить предложение\n"
            "  kb-agent self-improve propose-config <ключ> <старое> <новое> <причина>\n"
            "                                                              — предложить изменение конфига\n"
            "  kb-agent self-improve apply-config <id> <новое>             — применить предложение конфига\n"
            "  kb-agent self-improve rollback <имя> <версия>               — откат промпта к версии\n"
        )
    finally:
        state.close()


@app.command("list")
def self_improve_list(
    status: str = typer.Option(
        "proposed",
        "--status",
        "-s",
        help="Фильтр по статусу: proposed / confirmed / applied / rejected / failed",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Показать список предложений по улучшению."""
    allowed = ("proposed", "confirmed", "applied", "rejected", "failed")
    if status not in allowed:
        msg = f"Недопустимый статус: {status}. Доступны: {', '.join(allowed)}."
        if json_output:
            _output_json({"error": msg})
        else:
            typer.echo(msg, err=True)
        raise typer.Exit(code=1)

    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        engine = SelfImprovementEngine(state)
        proposals = engine.list_proposals(status=status)

        if json_output:
            _output_json(
                {
                    "status": status,
                    "count": len(proposals),
                    "proposals": [
                        {
                            "proposal_id": p.proposal_id,
                            "target": p.target,
                            "target_name": p.target_name,
                            "current_version": p.current_version,
                            "proposed_version": p.proposed_version,
                            "status": p.status,
                            "reason": p.reason,
                            "created_at": p.created_at,
                            "test_passed": p.test_passed,
                        }
                        for p in proposals
                    ],
                }
            )
            return

        if not proposals:
            typer.echo(f"Предложений со статусом '{status}' нет.")
            return

        typer.echo(f"Предложения ({status}, {len(proposals)}):")
        typer.echo("")
        for p in proposals:
            typer.echo(f"  ID: {p.proposal_id}")
            typer.echo(f"  Цель: {p.target} / {p.target_name}")
            typer.echo(f"  Версия: {p.current_version} → {p.proposed_version}")
            typer.echo(f"  Статус: {p.status}")
            typer.echo(f"  Причина: {p.reason}")
            typer.echo(f"  Создано: {p.created_at}")
            if p.test_passed is not None:
                typer.echo(f"  Тест пройден: {p.test_passed}")
            typer.echo("")
    finally:
        state.close()


@app.command("propose-prompt")
def self_improve_propose_prompt(
    prompt_name: str = typer.Argument(..., help="Имя промпта (например, agent_prompt)"),
    reason: str = typer.Argument(..., help="Причина предложения изменения"),
    content: str = typer.Argument(
        "", help="Новое содержимое промпта (игнорируется, если указан --content-file)"
    ),
    content_file: str = typer.Option(
        "",
        "--content-file",
        "-f",
        help="Путь к файлу с новым содержимым промпта",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Предложить изменение содержимого промпта (сохраняется в SQLite со статусом proposed)."""
    new_content = _read_content(content, content_file)
    if not new_content.strip():
        msg = "Ошибка: новое содержимое промпта не может быть пустым."
        if json_output:
            _output_json({"error": msg, "prompt_name": prompt_name})
        else:
            typer.echo(msg, err=True)
        raise typer.Exit(code=1)

    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        engine = SelfImprovementEngine(state)
        proposal = engine.propose_prompt_change(prompt_name, new_content, reason)

        if json_output:
            _output_json(
                {
                    "proposal_id": proposal.proposal_id,
                    "target": proposal.target,
                    "target_name": proposal.target_name,
                    "current_version": proposal.current_version,
                    "proposed_version": proposal.proposed_version,
                    "status": proposal.status,
                    "reason": proposal.reason,
                    "message": f"Предложение создано: {proposal.proposal_id}",
                }
            )
        else:
            typer.echo(f"✓ Предложение создано: {proposal.proposal_id}")
            typer.echo(f"  Промпт: {proposal.target_name}")
            typer.echo(f"  Версия: {proposal.current_version} → {proposal.proposed_version}")
            typer.echo(f"  Статус: {proposal.status}")
            typer.echo(f"  Причина: {proposal.reason}")
            typer.echo("")
            typer.echo(
                "Чтобы применить, выполните: "
                f"kb-agent self-improve apply-prompt {proposal.proposal_id}"
            )
    finally:
        state.close()


@app.command("apply-prompt")
def self_improve_apply_prompt(
    proposal_id: str = typer.Argument(..., help="ID предложения"),
    content: str = typer.Argument(
        "", help="Итоговое содержимое промпта (игнорируется, если указан --content-file)"
    ),
    content_file: str = typer.Option(
        "",
        "--content-file",
        "-f",
        help="Путь к файлу с итоговым содержимым промпта",
    ),
    confirmed_by: str = typer.Option(
        "user",
        "--confirmed-by",
        "-c",
        help="Кто подтверждает применение (по умолчанию user)",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Применить предложение по изменению промпта (после проверки тестовым набором)."""
    new_content = _read_content(content, content_file)
    if not new_content.strip():
        msg = "Ошибка: итоговое содержимое промпта не может быть пустым."
        if json_output:
            _output_json({"error": msg, "proposal_id": proposal_id})
        else:
            typer.echo(msg, err=True)
        raise typer.Exit(code=1)

    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        engine = SelfImprovementEngine(state)
        proposal = engine.get_proposal(proposal_id)
        if not proposal:
            msg = f"Предложение {proposal_id} не найдено."
            if json_output:
                _output_json({"error": msg, "proposal_id": proposal_id})
            else:
                typer.echo(msg, err=True)
            raise typer.Exit(code=1)

        proposal_obj = ImprovementProposal(
            proposal_id=proposal["proposal_id"],
            target=proposal["target"],
            target_name=proposal["target_name"],
            current_version=proposal["current_version"],
            proposed_version=proposal["proposed_version"],
            diff=proposal["diff"],
            reason=proposal["reason"],
            status=proposal["status"],
        )
        ok = engine.apply_prompt_proposal(
            proposal_obj, new_content, confirmed_by=confirmed_by
        )

        if json_output:
            _output_json(
                {
                    "proposal_id": proposal_id,
                    "target_name": proposal["target_name"],
                    "status": "applied" if ok else "failed",
                    "confirmed_by": confirmed_by,
                    "message": (
                        f"Предложение {proposal_id} применено."
                        if ok
                        else f"Предложение {proposal_id} не применено (проверьте статус)."
                    ),
                }
            )
        else:
            if ok:
                typer.echo(f"✓ Предложение {proposal_id} применено.")
                typer.echo(f"  Промпт: {proposal['target_name']}")
                typer.echo(f"  Подтвердил: {confirmed_by}")
            else:
                stored = engine.get_proposal(proposal_id)
                typer.echo(
                    f"✗ Предложение {proposal_id} не применено (статус: "
                    f"{stored['status'] if stored else 'неизвестен'}).",
                    err=True,
                )
                raise typer.Exit(code=1)
    finally:
        state.close()


@app.command("reject")
def self_improve_reject(
    proposal_id: str = typer.Argument(..., help="ID предложения"),
    reason: str = typer.Option(
        "", "--reason", "-r", help="Причина отклонения предложения"
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Отклонить предложение по улучшению."""
    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        engine = SelfImprovementEngine(state)
        proposal = engine.get_proposal(proposal_id)
        if not proposal:
            msg = f"Предложение {proposal_id} не найдено."
            if json_output:
                _output_json({"error": msg, "proposal_id": proposal_id})
            else:
                typer.echo(msg, err=True)
            raise typer.Exit(code=1)

        proposal_obj = ImprovementProposal(
            proposal_id=proposal["proposal_id"],
            target=proposal["target"],
            target_name=proposal["target_name"],
            current_version=proposal["current_version"],
            proposed_version=proposal["proposed_version"],
            diff=proposal["diff"],
            reason=proposal["reason"],
            status=proposal["status"],
        )
        engine.reject_proposal(proposal_obj)

        if json_output:
            _output_json(
                {
                    "proposal_id": proposal_id,
                    "status": "rejected",
                    "reject_reason": reason or proposal["reason"],
                    "message": f"Предложение {proposal_id} отклонено.",
                }
            )
        else:
            typer.echo(f"✓ Предложение {proposal_id} отклонено.")
            if reason:
                typer.echo(f"  Причина: {reason}")
    finally:
        state.close()


@app.command("propose-config")
def self_improve_propose_config(
    config_key: str = typer.Argument(..., help="Ключ конфигурации (например, openai_model)"),
    old_value: str = typer.Argument(..., help="Текущее значение"),
    new_value: str = typer.Argument(..., help="Предлагаемое новое значение"),
    reason: str = typer.Argument(..., help="Причина предложения изменения"),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Предложить изменение конфигурационного значения (сохраняется в SQLite)."""
    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        engine = SelfImprovementEngine(state)
        proposal = engine.propose_config_change(config_key, old_value, new_value, reason)

        if json_output:
            _output_json(
                {
                    "proposal_id": proposal.proposal_id,
                    "target": proposal.target,
                    "target_name": proposal.target_name,
                    "status": proposal.status,
                    "reason": proposal.reason,
                    "message": f"Предложение создано: {proposal.proposal_id}",
                }
            )
        else:
            typer.echo(f"✓ Предложение создано: {proposal.proposal_id}")
            typer.echo(f"  Ключ: {proposal.target_name}")
            typer.echo(f"  Статус: {proposal.status}")
            typer.echo(f"  Причина: {proposal.reason}")
            typer.echo("")
            typer.echo(
                "Чтобы применить, выполните: "
                f"kb-agent self-improve apply-config {proposal.proposal_id} <новое значение>"
            )
    finally:
        state.close()


@app.command("apply-config")
def self_improve_apply_config(
    proposal_id: str = typer.Argument(..., help="ID предложения"),
    new_value: str = typer.Argument(..., help="Итоговое значение конфигурации"),
    confirmed_by: str = typer.Option(
        "user",
        "--confirmed-by",
        "-c",
        help="Кто подтверждает применение (по умолчанию user)",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Применить предложение по изменению конфигурации (после проверки тестовым набором)."""
    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        engine = SelfImprovementEngine(state)
        proposal_row = engine.get_proposal(proposal_id)
        if not proposal_row:
            msg = f"Предложение {proposal_id} не найдено."
            if json_output:
                _output_json({"error": msg, "proposal_id": proposal_id})
            else:
                typer.echo(msg, err=True)
            raise typer.Exit(code=1)

        proposal_obj = ImprovementProposal(
            proposal_id=proposal_row["proposal_id"],
            target=proposal_row["target"],
            target_name=proposal_row["target_name"],
            current_version=proposal_row["current_version"],
            proposed_version=proposal_row["proposed_version"],
            diff=proposal_row["diff"],
            reason=proposal_row["reason"],
            status=proposal_row["status"],
        )
        ok = engine.apply_config_proposal(
            proposal_obj, new_value, confirmed_by=confirmed_by
        )

        if json_output:
            _output_json(
                {
                    "proposal_id": proposal_id,
                    "target_name": proposal_row["target_name"],
                    "status": "applied" if ok else "failed",
                    "confirmed_by": confirmed_by,
                    "message": (
                        f"Предложение {proposal_id} применено."
                        if ok
                        else f"Предложение {proposal_id} не применено (проверьте статус)."
                    ),
                }
            )
        else:
            if ok:
                typer.echo(f"✓ Предложение {proposal_id} применено.")
                typer.echo(f"  Конфиг: {proposal_row['target_name']}")
                typer.echo(f"  Подтвердил: {confirmed_by}")
            else:
                stored = engine.get_proposal(proposal_id)
                typer.echo(
                    f"✗ Предложение {proposal_id} не применено (статус: "
                    f"{stored['status'] if stored else 'неизвестен'}).",
                    err=True,
                )
                raise typer.Exit(code=1)
    finally:
        state.close()


@app.command("rollback")
def self_improve_rollback(
    prompt_name: str = typer.Argument(..., help="Имя промпта (например, agent_prompt)"),
    version: int = typer.Argument(..., help="Версия для отката"),
    json_output: bool = typer.Option(
        False,
        "--json",
        "-j",
        help="Вывести результат в формате JSON",
    ),
) -> None:
    """Откатить промпт к указанной версии (требует подтверждения)."""
    config = load_config()
    state = StateManager(config.db_path)
    try:
        state.init_db()
        engine = SelfImprovementEngine(state)
        current = state.get_active_prompt(prompt_name)
        if not current:
            msg = f"Активная версия промпта '{prompt_name}' не найдена."
            if json_output:
                _output_json({"error": msg, "prompt_name": prompt_name})
            else:
                typer.echo(msg, err=True)
            raise typer.Exit(code=1)

        if current["version"] == version:
            msg = f"Промпт '{prompt_name}' уже на версии {version}."
            if json_output:
                _output_json(
                    {
                        "prompt_name": prompt_name,
                        "current_version": current["version"],
                        "target_version": version,
                        "status": "unchanged",
                        "message": msg,
                    }
                )
            else:
                typer.echo(msg)
            return

        typer.echo(
            f"Откатить промпт '{prompt_name}' с версии {current['version']} на версию {version}?"
        )
        if not typer.confirm("Подтвердить откат?"):
            typer.echo("Откат отменён.")
            raise typer.Exit(code=0)

        success = engine.rollback_prompt(prompt_name, version, confirmed_by="user")
        if success:
            result = {
                "prompt_name": prompt_name,
                "from_version": current["version"],
                "to_version": version,
                "status": "rolled_back",
                "message": f"Промпт '{prompt_name}' откатан к версии {version}.",
            }
            if json_output:
                _output_json(result)
            else:
                typer.echo(f"✓ Промпт '{prompt_name}' откатан к версии {version}.")
        else:
            result = {
                "prompt_name": prompt_name,
                "from_version": current["version"],
                "to_version": version,
                "status": "failed",
                "message": f"Не удалось откатить промпт '{prompt_name}' к версии {version}.",
            }
            if json_output:
                _output_json(result)
            else:
                typer.echo(
                    f"✗ Не удалось откатить промпт '{prompt_name}' к версии {version}.",
                    err=True,
                )
            raise typer.Exit(code=1)
    finally:
        state.close()
