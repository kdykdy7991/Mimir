from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from src.application.services.worker_pool import (
    DurableWorkerPool, DurableWorkerStore, PermanentWorkerError,
)


def test_atomic_claim_only_has_one_winner(tmp_path) -> None:
    store = DurableWorkerStore(tmp_path / "workers.db")
    assert store.enqueue(
        "task-1", "parse", {"path": "safe-relative.pdf"}, available_at=1,
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        claims = list(pool.map(
            lambda n: store.claim("parse", f"worker-{n}", lease_seconds=30, now=10),
            range(8),
        ))
    assert len([claim for claim in claims if claim is not None]) == 1


def test_expired_lease_is_recovered_after_restart(tmp_path) -> None:
    path = tmp_path / "workers.db"
    first = DurableWorkerStore(path)
    first.enqueue("task-1", "sync", {"source_id": "source-1"}, available_at=1)
    lease = first.claim("sync", "old-process", lease_seconds=5, now=10)
    assert lease is not None
    restarted = DurableWorkerStore(path)
    assert restarted.claim("sync", "new-process", lease_seconds=5, now=14) is None
    recovered = restarted.claim("sync", "new-process", lease_seconds=5, now=15)
    assert recovered is not None
    assert recovered.attempt == 2


def test_heartbeat_extends_only_current_owners_live_lease(tmp_path) -> None:
    store = DurableWorkerStore(tmp_path / "workers.db")
    store.enqueue("task-1", "index", {}, available_at=1)
    assert store.claim("index", "owner", lease_seconds=5, now=10)
    assert store.heartbeat("task-1", "intruder", lease_seconds=5, now=12) is False
    assert store.heartbeat("task-1", "owner", lease_seconds=5, now=12) is True
    assert store.claim("index", "next", lease_seconds=5, now=16) is None
    assert store.claim("index", "next", lease_seconds=5, now=17) is not None


def test_pool_dispatches_and_completes_job(tmp_path) -> None:
    store = DurableWorkerStore(tmp_path / "workers.db")
    seen = []
    store.enqueue("task-1", "maintenance", {"operation": "compact"})
    pool = DurableWorkerPool(
        store,
        {"maintenance": lambda payload, ctx: (seen.append(payload), ctx.heartbeat())},
        owner="process-1",
    )
    assert pool.run_once("maintenance") is True
    assert seen == [{"operation": "compact"}]
    assert store.get("task-1")["state"] == "completed"


def test_transient_failure_uses_exponential_backoff_then_dead_letters(tmp_path) -> None:
    store = DurableWorkerStore(tmp_path / "workers.db")
    store.enqueue("task-1", "sync", {}, available_at=1, max_attempts=2)
    first = store.claim("sync", "owner", lease_seconds=10, now=10)
    assert first and first.attempt == 1
    assert store.fail(
        "task-1", "owner", error_class="transient", error_code="timeout",
        retryable=True, base_delay_seconds=2, now=10,
    ) == "queued"
    assert store.get("task-1")["available_at"] == 12
    second = store.claim("sync", "owner", lease_seconds=10, now=12)
    assert second and second.attempt == 2
    assert store.fail(
        "task-1", "owner", error_class="transient", error_code="timeout",
        retryable=True, now=12,
    ) == "dead_letter"


def test_permanent_failure_dead_letters_and_admin_can_replay(tmp_path) -> None:
    store = DurableWorkerStore(tmp_path / "workers.db")
    store.enqueue("task-1", "parse", {}, available_at=1)
    assert store.claim("parse", "owner", lease_seconds=10, now=10)
    assert store.fail(
        "task-1", "owner", error_class="permanent", error_code="bad_payload",
        retryable=False, now=11,
    ) == "dead_letter"
    assert store.replay_dead_letter("task-1", now=20)
    assert store.claim("parse", "owner-2", lease_seconds=10, now=20)
    assert [event["event"] for event in store.list_events("task-1")] == [
        "enqueued", "claimed", "dead_lettered", "replayed", "claimed",
    ]


def test_revision_idempotency_key_prevents_duplicate_execution(tmp_path) -> None:
    store = DurableWorkerStore(tmp_path / "workers.db")
    assert store.enqueue("task-1", "index", {}, idempotency_key="revision:r1")
    assert not store.enqueue("task-2", "index", {}, idempotency_key="revision:r1")


def test_pool_classifies_permanent_and_transient_failures(tmp_path) -> None:
    store = DurableWorkerStore(tmp_path / "workers.db")
    store.enqueue("permanent", "parse", {}, max_attempts=3)
    pool = DurableWorkerPool(
        store,
        {"parse": lambda payload, ctx: (_ for _ in ()).throw(PermanentWorkerError())},
        owner="worker",
    )
    assert pool.run_once("parse")
    assert store.get("permanent")["state"] == "dead_letter"
