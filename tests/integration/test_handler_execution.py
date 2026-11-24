"""Integration tests for job handler execution and worker state transitions."""

import uuid
from typing import Any

from sqlalchemy import select

from app.db.models import AttemptStatus, Job, JobAttempt, JobStatus
from app.handlers.registry import register_handler
from app.workers.worker import Worker
from tests.conftest import TestingSessionLocal


@register_handler("math_add")
def handle_math_add(payload: dict[str, Any]) -> dict[str, Any]:
    """Sample handler performing addition."""
    return {"sum": payload["a"] + payload["b"]}


def test_worker_executes_registered_handler_to_success() -> None:
    """Verify that a worker claims a job, executes the handler, and marks it SUCCEEDED."""
    job_id = uuid.uuid4()
    with TestingSessionLocal() as db:
        job = Job(
            id=job_id,
            job_type="math_add",
            payload={"a": 15, "b": 27},
            status=JobStatus.QUEUED.value,
        )
        db.add(job)
        db.commit()

    worker = Worker(worker_id="test-worker-1", session_factory=TestingSessionLocal)
    with TestingSessionLocal() as db:
        processed = worker.process_one_job(db)
        assert processed is True

    # Verify job status and result
    with TestingSessionLocal() as db:
        db_job = db.get(Job, job_id)
        assert db_job is not None
        assert db_job.status == JobStatus.SUCCEEDED.value
        assert db_job.result == {"sum": 42}
        assert db_job.locked_by is None
        assert db_job.lease_expires_at is None
        assert db_job.attempts == 1

        # Verify attempt record
        attempts = db.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id)).all()
        assert len(attempts) == 1
        assert attempts[0].status == AttemptStatus.SUCCEEDED.value
        assert attempts[0].result == {"sum": 42}
        assert attempts[0].worker_id == "test-worker-1"
