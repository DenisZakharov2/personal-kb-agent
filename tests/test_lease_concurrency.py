"""Волна 2: два процесса не обрабатывают одно задание одновременно (lease-защита)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kb_agent.storage.state import StateManager


def _insert_queue_item(
    state: StateManager,
    external_id: str,
    queue_state: str = "new",
    next_retry_at: str | None = None,
    lease_expires_at: str | None = None,
) -> int:
    conn = state.connect()
    conn.execute(
        """
        INSERT INTO raw_queue
            (source_id, external_id, canonical_url, title, raw_text, raw_hash,
             state, next_retry_at, lease_expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "src_test",
            external_id,
            "https://example.com/item",
            "Title",
            "raw text",
            f"hash-{external_id}",
            queue_state,
            next_retry_at,
            lease_expires_at,
        ),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM raw_queue WHERE external_id = ?", (external_id,)
    ).fetchone()
    assert row is not None
    return row["id"]


class TestLeaseProtection:
    """Параллельные воркеры не захватывают одно задание одновременно."""

    def test_two_workers_do_not_claim_same_job(self, config):
        """Первый claim_next забирает задание, второй получает None."""
        s1 = StateManager(config.db_path)
        s2 = StateManager(config.db_path)
        s1.init_db()
        s2.init_db()
        try:
            qid = _insert_queue_item(s1, "job-parallel-1")

            item1 = s1.claim_next()
            assert item1 is not None
            assert item1["id"] == qid
            # claim_next возвращает снимок до обновления; статус проверяем отдельно
            assert (
                s1.connect()
                .execute("SELECT state FROM raw_queue WHERE id = ?", (qid,))
                .fetchone()["state"]
                == "processing"
            )

            item2 = s2.claim_next()
            assert item2 is None, "Второй воркер не должен получить то же задание"
        finally:
            s1.close()
            s2.close()

    def test_claim_after_item_processed(self, config):
        """После завершения задание не попадает в повторную выборку как novo."""
        s1 = StateManager(config.db_path)
        s2 = StateManager(config.db_path)
        s1.init_db()
        s2.init_db()
        try:
            qid = _insert_queue_item(s1, "job-parallel-2")

            item1 = s1.claim_next()
            assert item1 is not None
            s1.mark_processed(qid)

            assert s2.claim_next() is None
        finally:
            s1.close()
            s2.close()

    def test_failed_without_next_retry_not_claimed(self, config):
        """Failed-элемент без next_retry_at не считается готовому к повтору."""
        s = StateManager(config.db_path)
        s.init_db()
        try:
            _insert_queue_item(s, "job-no-retry", queue_state="failed")
            assert s.claim_next() is None
        finally:
            s.close()

    def test_stuck_processing_released_after_lease(self, config):
        """Зависший processing с истёкшим lease доступен другому воркеру."""
        s1 = StateManager(config.db_path)
        s2 = StateManager(config.db_path)
        s1.init_db()
        s2.init_db()
        try:
            past = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
            qid = _insert_queue_item(s1, "job-stuck", queue_state="processing", lease_expires_at=past)

            item1 = s1.claim_next()
            assert item1 is not None
            assert item1["id"] == qid  # другой воркер забрал зависшее задание
        finally:
            s1.close()
            s2.close()