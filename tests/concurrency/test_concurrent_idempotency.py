"""Concurrency tests for duplicate submission idempotency."""

import concurrent.futures
import uuid
from collections.abc import Generator
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import IdempotencyKey, Job
from app.db.session import get_db
from app.main import app
from tests.conftest import TestingSessionLocal


def test_concurrent_duplicate_submissions_create_single_job() -> None:
    """Verify that concurrent requests with identical Idempotency-Key create exactly 1 job."""

    def override_get_db() -> Generator[Session, None, None]:
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    shared_key = f"concurrent-idem-{uuid.uuid4()}"
    payload = {
        "job_type": "test_job",
        "payload": {"data": "critical-order-42"},
        "priority": 5,
    }
    headers = {"Idempotency-Key": shared_key}

    num_concurrent_requests = 10
    responses: list[dict[str, Any]] = []

    def submit_request() -> dict[str, Any]:
        with TestClient(app) as client:
            res = client.post("/jobs", json=payload, headers=headers)
            return {"status_code": res.status_code, "data": res.json()}

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_concurrent_requests) as executor:
        futures = [executor.submit(submit_request) for _ in range(num_concurrent_requests)]
        for f in concurrent.futures.as_completed(futures):
            responses.append(f.result())

    app.dependency_overrides.clear()

    # Verify all responses succeeded (either 201 Created or 200 OK duplicate match)
    for r in responses:
        assert r["status_code"] in (200, 201)

    # Verify all responses returned the exact same job ID
    returned_ids = {r["data"]["job_id"] for r in responses}
    assert len(returned_ids) == 1, f"Multiple jobs were created: {returned_ids}"
    single_job_id = next(iter(returned_ids))

    # Verify database has exactly 1 job and 1 idempotency record
    with TestingSessionLocal() as db:
        job_count = db.scalar(select(func.count(Job.id)).where(Job.id == uuid.UUID(single_job_id)))
        assert job_count == 1

        idem_count = db.scalar(
            select(func.count(IdempotencyKey.id)).where(
                IdempotencyKey.idempotency_key == shared_key
            )
        )
        assert idem_count == 1
