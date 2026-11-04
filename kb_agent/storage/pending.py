"""Менеджер pending-запросов для write-операций (Этап 4).

Все изменения, вносимые через MCP-сервер, проходят через стадию ожидания
подтверждения человеком. Менеджер отвечает за создание, список, подтверждение
и применение pending-запросов.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
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

ALLOWED_STATUSES = ("pending", "confirmed", "applied", "rejected", "failed", "expired")


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
        *,
        diff: Optional[str] = None,
        created_by: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
    ) -> str:
        """Создаёт pending-запрос. Возвращает change_id.

        ``ttl_seconds`` задаёт срок жизни запроса: после истечения
        ``expires_at`` статус переходит в ``expired`` (см. ``expire_pending``)
        и предложение требует пересоздания.

        ``diff`` — текстовое представление предлагаемого изменения (для аудита и
        проверки при подтверждении). ``created_by`` — автор предложения.
        ``payload_hash`` (хранится как ``content_hash``) вычисляется из
        параметров при создании и сверяется при подтверждении.
        """
        if action not in ALLOWED_ACTIONS:
            raise ValueError(f"Недопустимое действие: {action}")

        import uuid

        change_id = str(uuid.uuid4())
        expires_at: Optional[str] = None
        if ttl_seconds is not None:
            expires_at = (
                datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
            ).isoformat()
        # payload_hash — хеш параметров предложения, вычисляется при создании
        # и сверяется при подтверждении (защита от подмены параметров между
        # созданием и подтверждением).
        payload_hash = hashlib.sha256(
            json.dumps(parameters, sort_keys=True, ensure_ascii=False).encode(
                "utf-8"
            )
        ).hexdigest()
        conn = self.state.connect()
        conn.execute(
            """
            INSERT INTO pending_changes
                (change_id, action, entity_type, entity_id, parameters, status,
                 revision, expires_at, diff, created_by, content_hash)
            VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?, ?)
            """,
            (
                change_id,
                action,
                entity_type,
                entity_id,
                json.dumps(parameters, ensure_ascii=False),
                expires_at,
                diff,
                created_by,
                payload_hash,
            ),
        )
        conn.commit()
        logger.info(
            "Создан pending-запрос %s: action=%s entity=%s/%s revision=0 ttl=%s "
            "payload_hash=%s created_by=%s",
            change_id,
            action,
            entity_type,
            entity_id,
            ttl_seconds,
            payload_hash[:12],
            created_by,
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

    def confirm_change(
        self, change_id: str, revision: int, confirmed_by: str = "user"
    ) -> bool:
        """Подтверждает и применяет pending-запрос с оптимистичной блокировкой.

        Проверка ``status = 'pending'``, совпадения ``revision`` и
        ``payload_hash`` (content_hash) выполняется атомарно в одной
        транзакции ``BEGIN IMMEDIATE``. При несовпадении ревизии или хеша
        параметров запрос не применяется и метод возвращает False.

        Возвращает True при успешном применении, False — при ошибке, если
        запрос не найден, уже не в статусе pending, ревизия не совпала или
        параметры были изменены после создания (payload_hash не совпал).
        """
        conn = self.state.connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT * FROM pending_changes WHERE change_id = ?", (change_id,)
            ).fetchone()
            if row is None:
                conn.rollback()
                logger.warning("Pending-запрос %s не найден", change_id)
                return False
            change = dict(row)
            if change["status"] != "pending":
                conn.rollback()
                logger.warning(
                    "Pending-запрос %s уже в статусе %s", change_id, change["status"]
                )
                return False
            if change["revision"] != revision:
                conn.rollback()
                logger.warning(
                    "Конфликт ревизии для %s: клиент=%s, в БД=%s",
                    change_id,
                    revision,
                    change["revision"],
                )
                return False

            # Проверяем payload_hash: параметры не должны меняться между
            # созданием предложения и его подтверждением.
            parameters = json.loads(change["parameters"] or "{}")
            current_hash = hashlib.sha256(
                json.dumps(parameters, sort_keys=True, ensure_ascii=False).encode(
                    "utf-8"
                )
            ).hexdigest()
            stored_hash = change["content_hash"] or ""
            if stored_hash and current_hash != stored_hash:
                conn.rollback()
                logger.warning(
                    "Конфликт payload_hash для %s: параметры изменены после "
                    "создания предложения", change_id
                )
                return False

            success = self._apply_change(change, conn=conn)

            now = datetime.now(timezone.utc).isoformat()
            content_hash = hashlib.sha256(
                json.dumps(parameters, sort_keys=True, ensure_ascii=False).encode(
                    "utf-8"
                )
            ).hexdigest()

            if success:
                conn.execute(
                    """
                    UPDATE pending_changes
                    SET status = 'applied', confirmed_at = ?, applied_at = ?,
                        confirmed_by = ?, content_hash = ?, revision = revision + 1
                    WHERE change_id = ?
                    """,
                    (now, now, confirmed_by, content_hash, change_id),
                )
                logger.info(
                    "Pending-запрос %s применён (%s, ревизия %s→%s)",
                    change_id,
                    confirmed_by,
                    revision,
                    revision + 1,
                )
            else:
                conn.execute(
                    """
                    UPDATE pending_changes
                    SET status = 'failed', confirmed_at = ?, confirmed_by = ?,
                        content_hash = ?, revision = revision + 1
                    WHERE change_id = ?
                    """,
                    (now, confirmed_by, content_hash, change_id),
                )
                logger.error("Pending-запрос %s не применён (ошибка)", change_id)
            conn.commit()
            return success
        except Exception:
            conn.rollback()
            logger.exception("Ошибка подтверждения pending-запроса %s", change_id)
            raise

    def reject_change(self, change_id: str, reason: Optional[str] = None) -> bool:
        """Отклоняет pending-запрос со статусом rejected и записью причины."""
        conn = self.state.connect()
        cur = conn.execute(
            """
            UPDATE pending_changes
            SET status = 'rejected', reject_reason = ?, revision = revision + 1
            WHERE change_id = ? AND status = 'pending'
            """,
            (reason, change_id),
        )
        conn.commit()
        if cur.rowcount > 0:
            logger.info("Pending-запрос %s отклонён (причина: %s)", change_id, reason)
        return cur.rowcount > 0

    def expire_pending(self, now: Optional[str] = None) -> int:
        """Переводит истёкшие по TTL pending-запросы в статус ``expired``.

        Возвращает количество переведённых записей. Предложение со статусом
        expired требует пересоздания.
        """
        if now is None:
            now = datetime.now(timezone.utc).isoformat()
        conn = self.state.connect()
        cur = conn.execute(
            """
            UPDATE pending_changes
            SET status = 'expired', revision = revision + 1
            WHERE status = 'pending'
              AND expires_at IS NOT NULL
              AND expires_at <= ?
            """,
            (now,),
        )
        conn.commit()
        if cur.rowcount > 0:
            logger.info("Истёкло pending-запросов: %s", cur.rowcount)
        return cur.rowcount

    # ---- Применение конкретных действий ----

    def _apply_change(self, change: dict, conn=None) -> bool:
        """Применяет подтверждённое изменение в зависимости от действия.

        Выполняется в переданном соединении (или собственном) без отдельного
        коммита, чтобы confirmaцияremaining оставалась атомарной транзакцией.
        """
        c = conn or self.state.connect()
        own = conn is None
        action = change["action"]
        parameters = json.loads(change["parameters"] or "{}")

        try:
            if action == "add_source":
                new_id = self.state.add_source(
                    source_id=parameters["source_id"],
                    source_type=parameters["source_type"],
                    channel=parameters["channel"],
                    enabled=parameters.get("enabled", True),
                    conn=c,
                )
                if new_id is None:
                    return self._apply_failed(
                        change, c, own,
                        f"Источник {parameters['source_id']} уже существует",
                    )
            elif action == "remove_source":
                if not self.state.remove_source(parameters["source_id"], conn=c):
                    return self._apply_failed(
                        change, c, own,
                        f"Источник {parameters['source_id']} не найден",
                    )
            elif action == "enable_source":
                if not self.state.set_source_enabled(
                    parameters["source_id"],
                    parameters.get("enabled", True),
                    conn=c,
                ):
                    return self._apply_failed(
                        change, c, own,
                        f"Источник {parameters['source_id']} не найден",
                    )
            elif action == "update_profile":
                self._update_profile(parameters)
            elif action == "revert_prompt":
                self._revert_prompt(parameters, conn=c)
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
            self._save_result(change["change_id"], result, conn=c)
            if own:
                c.rollback()
            return False

        result = {
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "status": "success",
        }
        self._save_result(change["change_id"], result, conn=c)
        if own:
            c.commit()
        return True

    def _apply_failed(
        self,
        change: dict,
        c: sqlite3.Connection,
        own: bool,
        error: str,
    ) -> bool:
        """Фиксирует неуспешное применение: пишет result со статусом failed и возвращает False.

        Вызывается, когда метод StateManager вернул «пустой» результат
        (None / False), т. е. фактически ничего не изменилось, и pending-запрос
        не должен получить ложный статус applied.
        """
        result = {
            "applied_at": datetime.now(timezone.utc).isoformat(),
            "status": "failed",
            "error": error,
        }
        self._save_result(change["change_id"], result, conn=c)
        if own:
            c.rollback()
        logger.error(
            "Pending-запрос %s не применён: %s", change["change_id"], error
        )
        return False

    def _save_result(
        self, change_id: str, result: dict, conn: Optional[sqlite3.Connection] = None
    ) -> None:
        c = conn or self.state.connect()
        c.execute(
            "UPDATE pending_changes SET result = ? WHERE change_id = ?",
            (json.dumps(result, ensure_ascii=False), change_id),
        )
        if conn is None:
            c.commit()

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

    def _revert_prompt(
        self, parameters: dict, conn: Optional[sqlite3.Connection] = None
    ) -> None:
        """Откатывает промпт к указанной (или предыдущей) версии."""
        c = conn or self.state.connect()
        own = conn is None
        prompt_name = parameters["prompt_name"]
        target_version = parameters.get("version")

        if target_version is None:
            # Находим текущую активную версию и берём предыдущую
            current = c.execute(
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

        target = c.execute(
            """
            SELECT * FROM prompt_history
            WHERE prompt_name = ? AND version = ?
            """,
            (prompt_name, target_version),
        ).fetchone()
        if not target:
            raise ValueError(f"Версия {target_version} промпта {prompt_name} не найдена")

        c.execute(
            "UPDATE prompt_history SET is_active = 0 WHERE prompt_name = ?",
            (prompt_name,),
        )
        c.execute(
            """
            UPDATE prompt_history
            SET is_active = 1
            WHERE prompt_name = ? AND version = ?
            """,
            (prompt_name, target_version),
        )
        if own:
            c.commit()
        logger.info("Промпт %s откатан к версии %s", prompt_name, target_version)
