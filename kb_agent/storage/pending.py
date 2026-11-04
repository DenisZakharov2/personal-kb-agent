"""Менеджер pending-запросов для write-операций (Этап 4).

Все изменения, вносимые через MCP-сервер, проходят через стадию ожидания
подтверждения человеком. Менеджер отвечает за создание, список, подтверждение
и применение pending-запросов.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

import yaml

from kb_agent.config import load_config
from kb_agent.storage.state import StateManager
from kb_agent.utils.logger import setup_logger

logger = setup_logger("kb_agent.storage.pending")

# Допустимые действия pending-запроса
ALLOWED_ACTIONS = (
    "add_source",
    "remove_source",
    "enable_source",
    "update_profile",
    "revert_prompt",
)

ALLOWED_STATUSES = ("pending", "confirmed", "applied", "rejected", "failed")


class PendingManager:
    """Менеджер pending-запросов для write-операций."""

    def __init__(self, state: StateManager) -> None:
        self.state = state

    # ---- Создание и чтение ----

    def create_change(
        self,
        action: str,
        entity_type: str,
        entity_id: str,
        parameters: dict,
    ) -> str:
        """Создаёт pending-запрос. Возвращает change_id."""
        if action not in ALLOWED_ACTIONS:
            raise ValueError(f"Недопустимое действие: {action}")

        import uuid

        change_id = str(uuid.uuid4())
        conn = self.state.connect()
        conn.execute(
            """
            INSERT INTO pending_changes (change_id, action, entity_type, entity_id, parameters, status)
            VALUES (?, ?, ?, ?, ?, 'pending')
            """,
            (
                change_id,
                action,
                entity_type,
                entity_id,
                json.dumps(parameters, ensure_ascii=False),
            ),
        )
        conn.commit()
        logger.info(
            "Создан pending-запрос %s: action=%s entity=%s/%s",
            change_id,
            action,
            entity_type,
            entity_id,
        )
        return change_id

    def get_change(self, change_id: str) -> Optional[dict]:
        """Возвращает pending-запрос по change_id или None."""
        conn = self.state.connect()
        row = conn.execute(
            "SELECT * FROM pending_changes WHERE change_id = ?",
            (change_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_changes(self, status: str = "pending") -> list[dict]:
        """Возвращает список pending-запросов со статусом ``status``."""
        if status not in ALLOWED_STATUSES:
            raise ValueError(f"Недопустимый статус: {status}")
        conn = self.state.connect()
        rows = conn.execute(
            "SELECT * FROM pending_changes WHERE status = ? ORDER BY created_at DESC",
            (status,),
        ).fetchall()
        return [dict(row) for row in rows]

    # ---- Подтверждение и применение ----

    def confirm_change(self, change_id: str, confirmed_by: str = "user") -> bool:
        """Подтверждает и применяет pending-запрос.

        Возвращает True при успешном применении, False — при ошибке или
        если запрос не найден / уже не в статусе pending.
        """
        change = self.get_change(change_id)
        if not change:
            logger.warning("Pending-запрос %s не найден", change_id)
            return False
        if change["status"] != "pending":
            logger.warning(
                "Pending-запрос %s уже в статусе %s", change_id, change["status"]
            )
            return False

        now = datetime.now(timezone.utc).isoformat()
        success = self._apply_change(change)

        conn = self.state.connect()
        if success:
            conn.execute(
                """
                UPDATE pending_changes
                SET status = 'applied', confirmed_at = ?, applied_at = ?, confirmed_by = ?
                WHERE change_id = ?
                """,
                (now, now, confirmed_by, change_id),
            )
            logger.info("Pending-запрос %s применён (%s)", change_id, confirmed_by)
        else:
            conn.execute(
                """
                UPDATE pending_changes
                SET status = 'failed', confirmed_at = ?, confirmed_by = ?
                WHERE change_id = ?
                """,
                (now, confirmed_by, change_id),
            )
            logger.error("Pending-запрос %s не применён (ошибка)", change_id)
        conn.commit()
        return success

    def reject_change(self, change_id: str) -> bool:
        """Отклоняет pending-запрос (статус → rejected)."""
        conn = self.state.connect()
        cur = conn.execute(
            """
            UPDATE pending_changes
            SET status = 'rejected'
            WHERE change_id = ? AND status = 'pending'
            """,
            (change_id,),
        )
        conn.commit()
        if cur.rowcount > 0:
            logger.info("Pending-запрос %s отклонён", change_id)
        return cur.rowcount > 0

    # ---- Применение конкретных действий ----

    def _apply_change(self, change: dict) -> bool:
        """Применяет подтверждённое изменение в зависимости от действия."""
        action = change["action"]
        parameters = json.loads(change["parameters"] or "{}")

        try:
            if action == "add_source":
                self.state.add_source(
                    source_id=parameters["source_id"],
                    source_type=parameters["source_type"],
                    channel=parameters["channel"],
                    enabled=parameters.get("enabled", True),
                )
            elif action == "remove_source":
                self.state.remove_source(parameters["source_id"])
            elif action == "enable_source":
                self.state.set_source_enabled(
                    parameters["source_id"],
                    parameters.get("enabled", True),
                )
            elif action == "update_profile":
                self._update_profile(parameters)
            elif action == "revert_prompt":
                self._revert_prompt(parameters)
            else:
                logger.error("Неизвестное действие: %s", action)
                return False

        except Exception as exc:  # noqa: BLE001
            logger.error("Ошибка применения изменения %s: %s", change["change_id"], exc)
            result = {
                "applied_at": datetime.now(timezone.utc).isoformat(),
                "status": "error",
                "error": str(exc),
            }
            self._save_result(change["change_id"], result)
            return False

        result = {
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "status": "success",
        }
        self._save_result(change["change_id"], result)
        return True

    def _save_result(self, change_id: str, result: dict) -> None:
        conn = self.state.connect()
        conn.execute(
            "UPDATE pending_changes SET result = ? WHERE change_id = ?",
            (json.dumps(result, ensure_ascii=False), change_id),
        )
        conn.commit()

    def _update_profile(self, parameters: dict) -> None:
        """Обновляет user_profile.yaml на основе параметров."""
        config = load_config()
        profile_path = config.profile_path

        if profile_path.exists():
            data = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
        else:
            data = {"version": 1, "user": {}}

        user = data.setdefault("user", {})
        updates = parameters.get("updates", {})
        if not isinstance(updates, dict):
            raise ValueError("Поле 'updates' должно быть объектом")
        user.update(updates)

        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(
            yaml.dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        logger.info("Профиль обновлён: %s", profile_path)

    def _revert_prompt(self, parameters: dict) -> None:
        """Откатывает промпт к указанной (или предыдущей) версии."""
        prompt_name = parameters["prompt_name"]
        target_version = parameters.get("version")

        conn = self.state.connect()

        if target_version is None:
            # Находим текущую активную версию и берём предыдущую
            current = conn.execute(
                """
                SELECT version FROM prompt_history
                WHERE prompt_name = ? AND is_active = 1
                ORDER BY version DESC LIMIT 1
                """,
                (prompt_name,),
            ).fetchone()
            if not current:
                # Ошибку пробрасываем наверх, чтобы pending-запрос получил статус failed,
                # а не «applied» при фактически неприменённом изменении.
                raise ValueError(f"Нет активной версии промпта {prompt_name}")
            target_version = current["version"] - 1

        target = conn.execute(
            """
            SELECT * FROM prompt_history
            WHERE prompt_name = ? AND version = ?
            """,
            (prompt_name, target_version),
        ).fetchone()
        if not target:
            raise ValueError(f"Версия {target_version} промпта {prompt_name} не найдена")

        conn.execute(
            "UPDATE prompt_history SET is_active = 0 WHERE prompt_name = ?",
            (prompt_name,),
        )
        conn.execute(
            """
            UPDATE prompt_history
            SET is_active = 1
            WHERE prompt_name = ? AND version = ?
            """,
            (prompt_name, target_version),
        )
        conn.commit()
        logger.info(
            "Промпт %s откатан к версии %s", prompt_name, target_version
        )
