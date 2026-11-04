"""Волна 1: атомарные pending-переходы, revision, TTL, reject, аудит-трейл (P2-02)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kb_agent.storage.pending import PendingManager


def _make_change(pm: PendingManager, parameters: dict | None = None) -> str:
    return pm.create_change(
        action="add_source",
        entity_type="source",
        entity_id="tg_test",
        parameters=parameters or {
            "source_id": "tg_test",
            "source_type": "telegram",
            "channel": "testchan",
        },
    )


class TestPendingRevision:
    """P2-02: confirm_change с оптимистичной ревизией (BEGIN IMMEDIATE)."""

    def test_confirm_change_ok_sets_audit_fields(self, config, state):
        """При успешном подтверждении: applied, ревизия +1, confirmed_by/at, content_hash."""
        pm = PendingManager(state)
        change_id = _make_change(pm)
        change = pm.get_change(change_id)
        assert change["revision"] == 0
        assert change["status"] == "pending"
        assert change["expires_at"] is None

        ok = pm.confirm_change(change_id, revision=0, confirmed_by="tester")
        assert ok is True

        updated = pm.get_change(change_id)
        assert updated["status"] == "applied"
        assert updated["revision"] == 1
        assert updated["confirmed_by"] == "tester"
        assert updated["confirmed_at"] is not None
        assert updated["applied_at"] is not None
        assert updated["content_hash"] is not None and len(updated["content_hash"]) == 64

    def test_confirm_change_wrong_revision_rejected(self, config, state):
        """Устаревшая ревизия → подтверждение отменено, статус остаётся pending."""
        pm = PendingManager(state)
        change_id = _make_change(pm)

        assert pm.confirm_change(change_id, revision=99) is False
        updated = pm.get_change(change_id)
        assert updated["status"] == "pending"
        assert updated["revision"] == 0

    def test_confirm_change_revision_mismatch_while_pending(self, config, state):
        """Если в БД уже другая ревизия, подтверждение со старой ревизией не проходит."""
        pm = PendingManager(state)
        change_id = _make_change(pm)

        conn = state.connect()
        conn.execute("UPDATE pending_changes SET revision = 5 WHERE change_id = ?", (change_id,))
        conn.commit()

        assert pm.confirm_change(change_id, revision=0) is False
        assert pm.get_change(change_id)["status"] == "pending"
        assert pm.get_change(change_id)["revision"] == 5

    def test_confirm_change_already_applied(self, config, state):
        """Повторное подтверждение того же change_id невозможно."""
        pm = PendingManager(state)
        change_id = _make_change(pm)

        assert pm.confirm_change(change_id, revision=0) is True
        assert pm.confirm_change(change_id, revision=1) is False

    def test_confirm_change_not_found(self, config, state):
        pm = PendingManager(state)
        assert pm.confirm_change("неизвестный-id", revision=0) is False

    def test_only_one_confirm_wins_optimistic_lock(self, config, state):
        """Два клиента с одной ревизией: подтвердит только первый."""
        pm = PendingManager(state)
        change_id = _make_change(pm)

        assert pm.confirm_change(change_id, revision=0) is True
        assert pm.confirm_change(change_id, revision=0) is False


class TestPendingReject:
    """P2-02: reject_change с записью причины."""

    def test_reject_change_records_reason(self, config, state):
        pm = PendingManager(state)
        change_id = pm.create_change(
            "remove_source", "source", "tg_test", {"source_id": "tg_test"}
        )

        assert pm.reject_change(change_id, reason="Не актуально") is True
        updated = pm.get_change(change_id)
        assert updated["status"] == "rejected"
        assert updated["reject_reason"] == "Не актуально"
        assert updated["revision"] == 1

    def test_reject_change_without_reason(self, config, state):
        pm = PendingManager(state)
        change_id = pm.create_change(
            "remove_source", "source", "tg_test", {"source_id": "tg_test"}
        )

        assert pm.reject_change(change_id) is True
        assert pm.get_change(change_id)["reject_reason"] is None
        assert pm.get_change(change_id)["status"] == "rejected"

    def test_reject_change_not_pending(self, config, state):
        pm = PendingManager(state)
        change_id = _make_change(pm)
        pm.confirm_change(change_id, revision=0)

        assert pm.reject_change(change_id, reason="поздно") is False
        assert pm.get_change(change_id)["status"] == "applied"


class TestPendingTTL:
    """P2-02: TTL pending-запроса — после истечения статус expired."""

    def test_create_change_with_ttl(self, config, state):
        pm = PendingManager(state)
        now = datetime.now(timezone.utc)
        change_id = pm.create_change(
            "add_source", "source", "tg_test", {"source_id": "tg_test"}, ttl_seconds=600
        )
        change = pm.get_change(change_id)
        assert change["expires_at"] is not None
        delta = (datetime.fromisoformat(change["expires_at"]) - now).total_seconds()
        assert 595 < delta < 605, change["expires_at"]

    def test_create_change_without_ttl(self, config, state):
        pm = PendingManager(state)
        change_id = pm.create_change("add_source", "source", "tg_test", {"source_id": "tg_test"})
        assert pm.get_change(change_id)["expires_at"] is None

    def test_expire_pending_marks_expired(self, config, state):
        pm = PendingManager(state)
        change_id = pm.create_change(
            "add_source", "source", "tg_test", {"source_id": "tg_test"}, ttl_seconds=1
        )
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        conn = state.connect()
        conn.execute(
            "UPDATE pending_changes SET expires_at = ? WHERE change_id = ?", (past, change_id)
        )
        conn.commit()

        expired = pm.expire_pending(now=past)
        assert expired == 1
        assert pm.get_change(change_id)["status"] == "expired"
        assert pm.get_change(change_id)["revision"] == 1

    def test_expire_pending_skips_fresh(self, config, state):
        pm = PendingManager(state)
        pm.create_change(
            "add_source", "source", "tg_test", {"source_id": "tg_test"}, ttl_seconds=3600
        )
        assert pm.expire_pending() == 0

    def test_expired_requires_recreate(self, config, state):
        """Запрос со статусом expired нельзя подтвердить — требуется пересоздание."""
        pm = PendingManager(state)
        change_id = _make_change(pm)
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        conn = state.connect()
        conn.execute(
            "UPDATE pending_changes SET status = 'expired', expires_at = ? WHERE change_id = ?",
            (past, change_id),
        )
        conn.commit()

        assert pm.confirm_change(change_id, revision=0) is False
        assert pm.get_change(change_id)["status"] == "expired"

    def test_list_changes_includes_expired(self, config, state):
        pm = PendingManager(state)
        change_id = _make_change(pm)
        conn = state.connect()
        conn.execute(
            "UPDATE pending_changes SET status = 'expired' WHERE change_id = ?", (change_id,)
        )
        conn.commit()

        assert pm.get_change(change_id)["status"] == "expired"
        expired = pm.list_changes(status="expired")
        assert any(c["change_id"] == change_id for c in expired)