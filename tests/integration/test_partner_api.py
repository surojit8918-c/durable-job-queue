"""Integration tests for Partner API handler and error handling."""

import uuid

from app.db.models import Job, JobStatus
from app.handlers.partner_api import mock_partner
from app.workers.worker import Worker
from tests.conftest import TestingSessionLocal


def test_partner_api_handler_success() -> None:
    """Verify that a partner API call succeeds and stores structured result."""
    job_id = uuid.uuid4()
    mock_partner.received_operations.clear()

    with TestingSessionLocal() as db:
        job = Job(
            id=job_id,
            job_type="partner_api_call",
            payload={"customer_id": 999, "operation": "sync_customer"},
            status=JobStatus.QUEUED.value,
        )
        db.add(job)
        db.commit()

    worker = Worker(worker_id="test-partner-worker", session_factory=TestingSessionLocal)
    with TestingSessionLocal() as db:
        processed = worker.process_one_job(db)
        assert processed is True

    with TestingSessionLocal() as db:
        refreshed = db.get(Job, job_id)
        assert refreshed is not None
        assert refreshed.status == JobStatus.SUCCEEDED.value
        assert refreshed.result is not None
        assert refreshed.result["partner_status"] == "success"

    # Verify mock partner recorded the operation across all tracking dimensions
    assert len(mock_partner.http_attempts) == 1
    assert len(mock_partner.received_operations) == 1
    assert len(mock_partner.successful_responses) == 1
    assert len(mock_partner.executed_business_operations) == 1
    assert mock_partner.received_operations[0]["endpoint"] == "sync_customer"


def test_partner_api_simulated_transient_failure_retries() -> None:
    """Verify that simulated transient partner failure causes job to enter RETRYING."""
    job_id = uuid.uuid4()

    with TestingSessionLocal() as db:
        job = Job(
            id=job_id,
            job_type="partner_api_call",
            payload={"customer_id": 888, "fail_retryable": True},
            status=JobStatus.QUEUED.value,
            max_attempts=3,
        )
        db.add(job)
        db.commit()

    worker = Worker(worker_id="test-partner-retry-worker", session_factory=TestingSessionLocal)
    with TestingSessionLocal() as db:
        processed = worker.process_one_job(db)
        assert processed is True

    with TestingSessionLocal() as db:
        refreshed = db.get(Job, job_id)
        assert refreshed is not None
        assert refreshed.status == JobStatus.RETRYING.value
        assert refreshed.attempts == 1


def test_partner_api_simulated_permanent_failure_terminates() -> None:
    """Verify that simulated permanent partner failure causes job to transition to FAILED."""
    job_id = uuid.uuid4()

    with TestingSessionLocal() as db:
        job = Job(
            id=job_id,
            job_type="partner_api_call",
            payload={"customer_id": 777, "fail_permanently": True},
            status=JobStatus.QUEUED.value,
            max_attempts=5,
        )
        db.add(job)
        db.commit()

    worker = Worker(worker_id="test-partner-fatal-worker", session_factory=TestingSessionLocal)
    with TestingSessionLocal() as db:
        processed = worker.process_one_job(db)
        assert processed is True

    with TestingSessionLocal() as db:
        refreshed = db.get(Job, job_id)
        assert refreshed is not None
        assert refreshed.status == JobStatus.FAILED.value
        assert refreshed.attempts == 1
