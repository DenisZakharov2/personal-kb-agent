"""P2-01: проверка возвращаемых значений StateManager при применении pending-изменений источников.

confirm_change должен переводить pending-запрос в статус ``failed`` с осмысленным
result, когда действие источника фактически не выполнено:
- add_source для уже существующего источника (add_source вернул None);
- remove_source для несуществующего источника (remove_source вернул False);
- enable_source для несуществующего источника (set_source_enabled вернул False).

Успешный путь по-прежнему даёт статус ``applied``.
"""

from __future__ import annotations

import json

import pytest

from kb_agent.storage.pending import PendingManager


def _source_params(source_id: str = "tg_test", channel: str = "testchan") -> dict:
    return {
        "source_id": source_id,
        "source_type": "telegram",
        "channel": channel,
        "enabled": True,
    }


def _create_add_source(pm: PendingManager, source_id: str = "tg_test") -> str:
    return pm.create_change(
        action="add_source",
        entity_type="source",
        entity_id=source_id,
        parameters=_source_params(source_id),
    )


def _create_remove_source(pm: PendingManager, source_id: str = "tg_test") -> str:
    return pm.create_change(
        action="remove_source",
        entity_type="source",
        entity_id=source_id,
        parameters={"source_id": source_id},
    )


def _create_enable_source(pm: PendingManager, source_id: str = "tg_test") -> str:
    return pm.create_change(
        action="enable_source",
        entity_type="source",
        entity_id=source_id,
        parameters={"source_id": source_id, "enabled": True},
    )


def _result_of(state, change_id: str) -> dict:
    row = state.connect().execute(
        "SELECT result FROM pending_changes WHERE change_id = ?", (change_id,)
    ).fetchone()
    assert row is not None
    return json.loads(row["result"])


class TestPendingAddSource:
    def test_add_source_duplicate_failed_no_duplicate(self, config, state):
        """add_source для существующего источника → failed, запись не дублируется."""
        state.add_source("tg_test", "telegram", "testchan")
        pm = PendingManager(state)
        change_id = _create_add_source(pm, "tg_test")

        assert pm.confirm_change(change_id, revision=0) is False

        updated = pm.get_change(change_id)
        assert updated["status"] == "failed"
        assert updated["revision"] == 1
        assert _result_of(state, change_id)["status"] == "failed"
        assert "уже существует" in _result_of(state, change_id)["error"]

        sources = state.list_sources()
        assert [s["source_id"] for s in sources] == ["tg_test"]

    def test_add_source_new_ok_applied(self, config, state):
        """Успешное добавление нового источника → applied и источник в списке."""
        pm = PendingManager(state)
        change_id = _create_add_source(pm, "tg_new")

        assert pm.confirm_change(change_id, revision=0) is True

        updated = pm.get_change(change_id)
        assert updated["status"] == "applied"
        assert _result_of(state, change_id)["status"] == "success"
        assert state.get_source("tg_new") is not None


class TestPendingRemoveSource:
    def test_remove_source_missing_failed(self, config, state):
        """remove_source для несуществующего источника → failed."""
        pm = PendingManager(state)
        change_id = _create_remove_source(pm, "no_such_source")

        assert pm.confirm_change(change_id, revision=0) is False

        updated = pm.get_change(change_id)
        assert updated["status"] == "failed"
        assert updated["revision"] == 1
        assert _result_of(state, change_id)["status"] == "failed"
        assert "не найден" in _result_of(state, change_id)["error"]

    def test_remove_source_existing_ok_applied(self, config, state):
        """Успешное удаление существующего источника → applied."""
        state.add_source("tg_test", "telegram", "testchan")
        pm = PendingManager(state)
        change_id = _create_remove_source(pm, "tg_test")

        assert pm.confirm_change(change_id, revision=0) is True

        assert pm.get_change(change_id)["status"] == "applied"
        assert state.get_source("tg_test") is None


class TestPendingEnableSource:
    def test_enable_source_missing_failed(self, config, state):
        """enable_source для несуществующего источника → failed."""
        pm = PendingManager(state)
        change_id = _create_enable_source(pm, "no_such_source")

        assert pm.confirm_change(change_id, revision=0) is False

        updated = pm.get_change(change_id)
        assert updated["status"] == "failed"
        assert _result_of(state, change_id)["status"] == "failed"
        assert "не найден" in _result_of(state, change_id)["error"]

    def test_enable_source_existing_ok_applied(self, config, state):
        """Успешное включение существующего источника → applied."""
        state.add_source("tg_test", "telegram", "testchan", enabled=False)
        pm = PendingManager(state)
        change_id = _create_enable_source(pm, "tg_test")

        assert pm.confirm_change(change_id, revision=0) is True

        assert pm.get_change(change_id)["status"] == "applied"
        assert state.get_source("tg_test")["enabled"] == 1
