"""Unit tests for idempotency request hashing."""

from datetime import UTC, datetime

from app.queue.idempotency import compute_request_hash


def test_compute_request_hash_determinism_and_formatting() -> None:
    """Verify that compute_request_hash is deterministic and unaffected by dictionary key ordering."""
    now = datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC)

    payload_a = {"user_id": 101, "action": "charge", "amount": 99.50}
    payload_b = {"amount": 99.50, "user_id": 101, "action": "charge"}  # Reordered keys

    hash_a = compute_request_hash("payment", payload_a, priority=5, run_at=now)
    hash_b = compute_request_hash("payment", payload_b, priority=5, run_at=now)

    # Hashes must be identical regardless of key insertion order
    assert hash_a == hash_b
    assert len(hash_a) == 64  # Valid SHA-256 hex string


def test_compute_request_hash_differentiates_changes() -> None:
    """Verify that any variance in payload, priority, job_type, or run_at yields a distinct hash."""
    now = datetime(2026, 11, 20, 10, 0, 0, tzinfo=UTC)
    base_hash = compute_request_hash("sync", {"id": 1}, priority=0, run_at=now)

    # Different job_type
    assert compute_request_hash("other_type", {"id": 1}, priority=0, run_at=now) != base_hash

    # Different payload
    assert compute_request_hash("sync", {"id": 2}, priority=0, run_at=now) != base_hash

    # Different priority
    assert compute_request_hash("sync", {"id": 1}, priority=1, run_at=now) != base_hash

    # Different run_at
    later = datetime(2026, 11, 20, 11, 0, 0, tzinfo=UTC)
    assert compute_request_hash("sync", {"id": 1}, priority=0, run_at=later) != base_hash

    # None run_at vs specified run_at
    assert compute_request_hash("sync", {"id": 1}, priority=0, run_at=None) != base_hash
