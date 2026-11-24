"""Integration tests for automatic retry handling, jitter, and error classification."""

import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select

from app.db.models import AttemptStatus, Job, JobAttempt, JobStatus
from app.handlers.registry import register_handler
from app.queue.retry import (
    PermanentJobError,
    RetryableJobError,
    calculate_retry_delay,
    classify_error,
)
from app.workers.worker import Worker
from tests.conftest import TestingSessionLocal


@register_handler("flaky_job")
def handle_flaky_job(payload: dict[str, Any]) -> dict[str, Any]:
    """Fails if attempt count is less than target, otherwise succeeds."""
    if payload.get("should_fail"):
        raise RetryableJobError("Transient network failure simulated")
    return {"status": "ok"}


@register_handler("fatal_job")
def handle_fatal_job(payload: dict[str, Any]) -> dict[str, Any]:
    """Simulates a non-retryable permanent error."""
    raise PermanentJobError("Invalid business account ID")


def test_retryable_error_schedules_retry() -> None:
    """Verify that a retryable error moves job from RUNNING to RETRYING with delay."""
    job_id = uuid.uuid4()
    with TestingSessionLocal() as db:
        job = Job(
            id=job_id,
            job_type="flaky_job",
            payload={"should_fail": True},
            status=JobStatus.QUEUED.value,
            max_attempts=3,
        )
        db.add(job)
        db.commit()

    worker = Worker(worker_id="test-retry-worker", session_factory=TestingSessionLocal)
    with TestingSessionLocal() as db:
        processed = worker.process_one_job(db)
        assert processed is True

    with TestingSessionLocal() as db:
        refreshed = db.get(Job, job_id)
        assert refreshed is not None
        assert refreshed.status == JobStatus.RETRYING.value
        assert refreshed.attempts == 1
        assert refreshed.available_at > datetime.now(UTC)
        assert refreshed.last_error is not None
        assert "Transient network failure" in refreshed.last_error

        # Attempt record marked FAILED
        attempt = db.scalar(select(JobAttempt).where(JobAttempt.job_id == job_id))
        assert attempt is not None
        assert attempt.status == AttemptStatus.FAILED.value


def test_permanent_error_transitions_to_failed_immediately() -> None:
    """Verify that a permanent error transitions immediately to FAILED without retrying."""
    job_id = uuid.uuid4()
    with TestingSessionLocal() as db:
        job = Job(
            id=job_id,
            job_type="fatal_job",
            payload={},
            status=JobStatus.QUEUED.value,
            max_attempts=5,
        )
        db.add(job)
        db.commit()

    worker = Worker(worker_id="test-fatal-worker", session_factory=TestingSessionLocal)
    with TestingSessionLocal() as db:
        processed = worker.process_one_job(db)
        assert processed is True

    with TestingSessionLocal() as db:
        refreshed = db.get(Job, job_id)
        assert refreshed is not None
        assert refreshed.status == JobStatus.FAILED.value
        assert refreshed.attempts == 1
        assert refreshed.last_error is not None
        assert "Invalid business account ID" in refreshed.last_error


def test_retry_after_header_override() -> None:
    """Verify HTTP 429 Retry-After header parsing and delay calculation."""
    request = httpx.Request("POST", "https://api.partner.com/test")
    response_429 = httpx.Response(
        status_code=429,
        headers={"Retry-After": "15"},
        request=request,
    )
    http_err = httpx.HTTPStatusError("Rate limited", request=request, response=response_429)

    is_retryable, retry_after = classify_error(http_err)
    assert is_retryable is True
    assert retry_after == 15.0

    delay = calculate_retry_delay(attempt=1, retry_after=retry_after)
    assert delay == 15.0
