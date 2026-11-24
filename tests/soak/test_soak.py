"""Automated soak test verifying multi-worker throughput and durability under load."""

import concurrent.futures
import random
import time
import uuid

from sqlalchemy import func, select

from app.db.models import Job, JobAttempt, JobStatus
from app.handlers.partner_api import mock_partner
from app.workers.worker import Worker
from tests.conftest import TestingSessionLocal


def test_soak_mini_batch() -> None:
    """Run a multi-worker load test with simulated transient errors, verifying 100% durability and accounting."""
    num_jobs = 150
    num_workers = 4
    mock_partner.received_operations.clear()
    mock_partner.failure_rate = 0.15  # 15% transient failures to trigger retries

    # 1. Enqueue batch of jobs
    job_ids: list[uuid.UUID] = []
    with TestingSessionLocal() as db:
        for i in range(num_jobs):
            j_id = uuid.uuid4()
            job = Job(
                id=j_id,
                job_type="partner_api_call",
                payload={"customer_id": 1000 + i, "order_id": i},
                status=JobStatus.QUEUED.value,
                priority=random.randint(0, 10),
                max_attempts=5,
            )
            db.add(job)
            job_ids.append(j_id)
        db.commit()

    # 2. Concurrently run 4 worker loops until all jobs reach terminal states
    deadline = time.time() + 20.0

    def worker_thread(w_id: str) -> int:
        worker = Worker(
            worker_id=w_id,
            poll_interval=0.02,
            lease_duration=10,
            base_retry_delay=0.05,
            max_retry_delay=0.2,
            session_factory=TestingSessionLocal,
        )
        processed_count = 0
        while time.time() < deadline:
            with TestingSessionLocal() as db:
                unfinished = (
                    db.scalar(
                        select(func.count(Job.id)).where(
                            Job.id.in_(job_ids),
                            Job.status.in_(
                                [
                                    JobStatus.QUEUED.value,
                                    JobStatus.RUNNING.value,
                                    JobStatus.RETRYING.value,
                                ]
                            ),
                        )
                    )
                    or 0
                )
                if unfinished == 0:
                    break

                did_work = worker.process_one_job(db)
                if did_work:
                    processed_count += 1
                else:
                    time.sleep(0.05)
        return processed_count

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = [executor.submit(worker_thread, f"soak-worker-{w}") for w in range(num_workers)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    # 3. Durability and accounting verification
    with TestingSessionLocal() as db:
        persisted_count = db.scalar(select(func.count(Job.id)).where(Job.id.in_(job_ids)))
        assert persisted_count == num_jobs, "Some accepted jobs were lost!"

        succeeded_count = (
            db.scalar(
                select(func.count(Job.id)).where(
                    Job.id.in_(job_ids),
                    Job.status == JobStatus.SUCCEEDED.value,
                )
            )
            or 0
        )

        failed_count = (
            db.scalar(
                select(func.count(Job.id)).where(
                    Job.id.in_(job_ids),
                    Job.status == JobStatus.FAILED.value,
                )
            )
            or 0
        )

        total_attempts = (
            db.scalar(select(func.count(JobAttempt.id)).where(JobAttempt.job_id.in_(job_ids))) or 0
        )

        # Every accepted job must be either SUCCEEDED or FAILED
        assert succeeded_count + failed_count == num_jobs, (
            f"Jobs left in unfinalized states! Succeeded={succeeded_count}, Failed={failed_count}, Total={num_jobs}"
        )
        assert total_attempts >= num_jobs, "Attempts count must be at least total jobs"

        # Partner API verification across three distinct tracking dimensions:
        # 1. Total HTTP request attempts matches total worker execution attempts (including transient 503 retries)
        assert len(mock_partner.http_attempts) == total_attempts
        assert len(mock_partner.received_operations) >= succeeded_count

        # 2. Every succeeded job received a corresponding successful partner outcome
        assert len(mock_partner.successful_responses) == succeeded_count

        # 3. Idempotency prevents duplicate side-effects: each business operation executed exactly once
        assert len(mock_partner.executed_business_operations) == succeeded_count
        assert len(mock_partner.processed_keys) == succeeded_count

    # Reset mock partner
    mock_partner.failure_rate = 0.0
