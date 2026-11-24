"""Integration tests for all FastAPI endpoints."""

import uuid
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db.models import Job, JobStatus
from app.db.session import get_db
from app.main import app
from tests.conftest import TestingSessionLocal


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    """TestClient configured with overridden test database session."""

    def override_get_db() -> Generator[Session, None, None]:
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_health_check(client: TestClient) -> None:
    """Verify health endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_submit_job_valid(client: TestClient) -> None:
    """Verify standard job submission commits to DB and returns 201."""
    payload = {
        "job_type": "test_job",
        "payload": {"order_id": 1001, "customer": "Alice"},
        "priority": 5,
    }
    response = client.post("/jobs", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert "job_id" in data
    assert data["status"] == "QUEUED"

    # Verify retrieval
    job_id = data["job_id"]
    get_res = client.get(f"/jobs/{job_id}")
    assert get_res.status_code == 200
    job_data = get_res.json()
    assert job_data["job_id"] == job_id
    assert job_data["job_type"] == "test_job"
    assert job_data["status"] == "QUEUED"
    assert job_data["priority"] == 5
    assert job_data["attempts"] == 0
    assert job_data["max_attempts"] == 5
    assert "created_at" in job_data and job_data["created_at"] is not None
    assert "updated_at" in job_data and job_data["updated_at"] is not None


def test_submit_job_unregistered_type(client: TestClient) -> None:
    """Verify that submitting an unregistered job type returns 422."""
    payload = {
        "job_type": "arbitrary_unregistered_type",
        "payload": {"foo": "bar"},
    }
    response = client.post("/jobs", json=payload)
    assert response.status_code == 422
    assert "is not registered" in response.json()["detail"]


def test_submission_idempotency(client: TestClient) -> None:
    """Verify idempotent submission and conflict detection."""
    key = f"idem-key-{uuid.uuid4()}"
    payload = {
        "job_type": "test_job",
        "payload": {"action": "sync", "user_id": 42},
        "priority": 1,
    }
    headers = {"Idempotency-Key": key}

    # First submission -> creates job
    res1 = client.post("/jobs", json=payload, headers=headers)
    assert res1.status_code == 201
    job_id_1 = res1.json()["job_id"]

    # Duplicate submission with identical body -> returns existing job
    res2 = client.post("/jobs", json=payload, headers=headers)
    assert res2.status_code == 200 or res2.status_code == 201
    assert res2.json()["job_id"] == job_id_1

    # Conflicting submission with same key but different body -> 409 Conflict
    conflicting_payload = {
        "job_type": "test_job",
        "payload": {"action": "sync", "user_id": 99999},  # Changed!
        "priority": 1,
    }
    res3 = client.post("/jobs", json=conflicting_payload, headers=headers)
    assert res3.status_code == 409
    assert "conflict" in res3.json()["detail"].lower()


def test_get_job_not_found(client: TestClient) -> None:
    """Verify 404 for nonexistent job."""
    missing_id = uuid.uuid4()
    response = client.get(f"/jobs/{missing_id}")
    assert response.status_code == 404


def test_get_stats(client: TestClient, db_session: Session) -> None:
    """Verify /stats operational statistics calculation."""
    # Seed known states
    j1 = Job(job_type="test_job", payload={}, status=JobStatus.QUEUED.value)
    j2 = Job(job_type="test_job", payload={}, status=JobStatus.RUNNING.value)
    j3 = Job(job_type="test_job", payload={}, status=JobStatus.SUCCEEDED.value)
    j4 = Job(job_type="test_job", payload={}, status=JobStatus.FAILED.value)
    db_session.add_all([j1, j2, j3, j4])
    db_session.commit()

    response = client.get("/stats")
    assert response.status_code == 200
    stats = response.json()
    assert stats["total_jobs"] >= 4
    assert stats["queued_jobs"] >= 1
    assert stats["running_jobs"] >= 1
    assert stats["succeeded_jobs"] >= 1
    assert stats["failed_jobs"] >= 1
    assert "expired_leases" in stats
    assert "oldest_queued_job_age_seconds" in stats


def test_manual_retry_operator_endpoint(client: TestClient, db_session: Session) -> None:
    """Verify manual retry of failed jobs and rejection of non-failed jobs."""
    # 1. Job in QUEUED status cannot be retried
    queued_job = Job(job_type="test_job", payload={}, status=JobStatus.QUEUED.value)
    db_session.add(queued_job)
    db_session.commit()

    res_fail = client.post(f"/jobs/{queued_job.id}/retry")
    assert res_fail.status_code == 400
    assert "Only 'FAILED' jobs can be retried" in res_fail.json()["detail"]

    # 2. Job in FAILED status can be retried
    failed_job = Job(
        job_type="test_job",
        payload={"task": 1},
        status=JobStatus.FAILED.value,
        attempts=5,
        locked_by="old-worker",
    )
    db_session.add(failed_job)
    db_session.commit()

    res_success = client.post(f"/jobs/{failed_job.id}/retry")
    assert res_success.status_code == 200
    data = res_success.json()
    assert data["job_id"] == str(failed_job.id)
    assert data["status"] == "QUEUED"

    # Verify state in DB
    db_session.refresh(failed_job)
    assert failed_job.status == JobStatus.QUEUED.value
    assert failed_job.attempts == 0
    assert failed_job.locked_by is None
    assert failed_job.lease_expires_at is None
