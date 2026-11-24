"""Concurrency tests for atomic job claiming."""

import concurrent.futures
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.models import Job, JobAttempt, JobStatus
from app.queue.claiming import claim_next_job
from tests.conftest import TestingSessionLocal


def test_concurrent_workers_claim_distinct_jobs() -> None:
    """Verify that multiple concurrent workers never claim the same available job."""
    num_jobs = 20
    num_workers = 5

    # 1. Seed jobs into database
    with TestingSessionLocal() as db:
        for i in range(num_jobs):
            job = Job(
                job_type="test_job",
                payload={"index": i},
                status=JobStatus.QUEUED.value,
                priority=i,
            )
            db.add(job)
        db.commit()

    claimed_job_ids: list[str] = []
    worker_assignments: dict[str, list[str]] = {}

    def worker_claim_loop(worker_name: str) -> list[str]:
        claims: list[str] = []
        with TestingSessionLocal() as db:
            while True:
                claimed = claim_next_job(db, worker_id=worker_name, lease_duration_seconds=30)
                if claimed is None:
                    break
                job, attempt = claimed
                claims.append(str(job.id))
        return claims

    # 2. Run workers concurrently using ThreadPoolExecutor
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {
            executor.submit(worker_claim_loop, f"worker-{w}"): f"worker-{w}"
            for w in range(num_workers)
        }
        for future in concurrent.futures.as_completed(futures):
            worker_name = futures[future]
            worker_assignments[worker_name] = future.result()
            claimed_job_ids.extend(worker_assignments[worker_name])

    # 3. Verify exactly num_jobs claimed with zero duplicates
    assert len(claimed_job_ids) == num_jobs
    assert len(set(claimed_job_ids)) == num_jobs, "Duplicate claim detected across workers!"

    # 4. Verify DB state
    with TestingSessionLocal() as db:
        attempts = db.scalars(select(JobAttempt)).all()
        assert len(attempts) == num_jobs
        for att in attempts:
            assert att.status == "RUNNING"
            assert att.attempt_number == 1
            assert att.lease_token == 1


def test_priority_and_scheduling_order() -> None:
    """Verify jobs are claimed strictly by priority and scheduled availability."""
    now = datetime.now(UTC)

    with TestingSessionLocal() as db:
        # Future scheduled job (should not be claimed yet)
        future_job = Job(
            job_type="test_job",
            payload={"tag": "future"},
            priority=100,  # Highest priority, but not yet available
            available_at=now + timedelta(hours=1),
            status=JobStatus.QUEUED.value,
        )
        # Normal priority jobs
        low_job = Job(
            job_type="test_job",
            payload={"tag": "low"},
            priority=1,
            available_at=now - timedelta(seconds=10),
            status=JobStatus.QUEUED.value,
        )
        high_job = Job(
            job_type="test_job",
            payload={"tag": "high"},
            priority=10,
            available_at=now - timedelta(seconds=10),
            status=JobStatus.QUEUED.value,
        )
        db.add_all([future_job, low_job, high_job])
        db.commit()

    with TestingSessionLocal() as db:
        # First claim should be high_job (priority 10)
        claim1 = claim_next_job(db, worker_id="worker-test")
        assert claim1 is not None
        assert claim1[0].payload["tag"] == "high"

        # Second claim should be low_job (priority 1)
        claim2 = claim_next_job(db, worker_id="worker-test")
        assert claim2 is not None
        assert claim2[0].payload["tag"] == "low"

        # Third claim should be None because future_job is not yet available
        claim3 = claim_next_job(db, worker_id="worker-test")
        assert claim3 is None
