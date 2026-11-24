"""Integration tests for database models and constraints."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AttemptStatus, IdempotencyKey, Job, JobAttempt, JobStatus


def test_create_and_retrieve_job(db_session: Session) -> None:
    """Verify that a job can be created and retrieved with all fields intact."""
    job_id = uuid.uuid4()
    job = Job(
        id=job_id,
        job_type="test_job",
        payload={"key": "value", "count": 42},
        status=JobStatus.QUEUED.value,
        priority=10,
        max_attempts=3,
    )
    db_session.add(job)
    db_session.commit()

    retrieved = db_session.scalar(select(Job).where(Job.id == job_id))
    assert retrieved is not None
    assert retrieved.id == job_id
    assert retrieved.job_type == "test_job"
    assert retrieved.payload == {"key": "value", "count": 42}
    assert retrieved.status == JobStatus.QUEUED.value
    assert retrieved.priority == 10
    assert retrieved.attempts == 0
    assert retrieved.max_attempts == 3
    assert retrieved.lease_token == 0
    assert retrieved.created_at is not None
    assert retrieved.updated_at is not None


def test_create_job_with_attempt_and_idempotency(db_session: Session) -> None:
    """Verify job attempts and idempotency relationships."""
    job = Job(
        job_type="partner_api_call",
        payload={"order_id": 123},
        status=JobStatus.RUNNING.value,
        attempts=1,
        locked_by="worker-test-1",
        lease_token=1,
    )
    db_session.add(job)
    db_session.flush()

    attempt = JobAttempt(
        job_id=job.id,
        attempt_number=1,
        worker_id="worker-test-1",
        status=AttemptStatus.RUNNING.value,
        lease_token=1,
    )
    db_session.add(attempt)

    idempotency = IdempotencyKey(
        idempotency_key="client-req-001",
        request_hash="a" * 64,
        job_id=job.id,
    )
    db_session.add(idempotency)
    db_session.commit()

    # Query back
    job_with_history = db_session.scalar(select(Job).where(Job.id == job.id))
    assert job_with_history is not None
    assert len(job_with_history.attempts_history) == 1
    assert job_with_history.attempts_history[0].worker_id == "worker-test-1"
    assert job_with_history.attempts_history[0].status == AttemptStatus.RUNNING.value

    stored_key = db_session.scalar(
        select(IdempotencyKey).where(IdempotencyKey.idempotency_key == "client-req-001")
    )
    assert stored_key is not None
    assert stored_key.job_id == job.id
