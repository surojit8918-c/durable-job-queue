"""Chaos test running a worker in a separate OS subprocess and killing it abruptly."""

import os
import subprocess
import sys
import textwrap
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.config import settings
from app.db.models import AttemptStatus, Job, JobAttempt, JobStatus
from app.queue.recovery import recover_expired_leases
from app.workers.worker import Worker
from tests.conftest import TestingSessionLocal


def test_subprocess_worker_crash_and_recovery() -> None:
    """Launch a worker in an OS subprocess, kill it with SIGKILL during execution, and recover job."""
    job_id = uuid.uuid4()
    lease_duration = 3  # 3 seconds lease for fast test

    # 1. Enqueue a job that simulates long work
    with TestingSessionLocal() as db:
        job = Job(
            id=job_id,
            job_type="test_job",
            payload={"message": "work_in_progress", "wait": True},
            status=JobStatus.QUEUED.value,
            max_attempts=3,
        )
        db.add(job)
        db.commit()

    # 2. Spawn worker in a separate subprocess
    worker_env = os.environ.copy()
    worker_env["DATABASE_URL"] = settings.TEST_DATABASE_URL
    worker_env["LEASE_DURATION_SECONDS"] = str(lease_duration)
    worker_env["LEASE_RENEWAL_INTERVAL_SECONDS"] = "1"
    worker_env["WORKER_POLL_INTERVAL"] = "0.1"

    worker_code = textwrap.dedent("""
    import time
    from app.handlers.registry import register_handler
    from app.workers.worker import Worker

    @register_handler("test_job")
    def handle_slow_job(payload):
        time.sleep(15)  # Hang until killed
        return {"done": True}

    worker = Worker(worker_id="crashed-subprocess-worker", lease_duration=3, poll_interval=0.1)
    worker.run()
    """)

    proc = subprocess.Popen(
        [sys.executable, "-c", worker_code],
        env=worker_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        # Wait until worker has claimed the job
        claimed = False
        for _ in range(30):
            time.sleep(0.2)
            with TestingSessionLocal() as db:
                db_job = db.get(Job, job_id)
                if db_job and db_job.status == JobStatus.RUNNING.value:
                    claimed = True
                    break

        assert claimed, "Subprocess worker failed to claim the job within timeout"

        # 3. Forcibly terminate (SIGKILL) worker process
        proc.kill()
        proc.wait(timeout=5)

        # 4. Wait for lease to expire
        time.sleep(lease_duration + 1.0)

        # 5. Run recovery
        with TestingSessionLocal() as db:
            recovered = recover_expired_leases(db, base_retry_delay_seconds=0.1)
            assert job_id in recovered

            refreshed = db.get(Job, job_id)
            assert refreshed is not None
            assert refreshed.status == JobStatus.RETRYING.value
            assert refreshed.locked_by is None

            # Attempt marked ABANDONED
            attempt = db.scalar(
                select(JobAttempt)
                .where(JobAttempt.job_id == job_id)
                .order_by(JobAttempt.attempt_number.desc())
            )
            assert attempt is not None
            assert attempt.status == AttemptStatus.ABANDONED.value

            # Reset availability for immediate pickup
            refreshed.available_at = datetime.now(UTC) - timedelta(seconds=1)
            db.commit()

        # 6. Fresh worker claims and finishes the job
        clean_worker = Worker(
            worker_id="clean-recovery-worker", session_factory=TestingSessionLocal
        )
        with TestingSessionLocal() as db:
            clean_processed = clean_worker.process_one_job(db)
            assert clean_processed is True

        with TestingSessionLocal() as db:
            final_job = db.get(Job, job_id)
            assert final_job is not None
            assert final_job.status == JobStatus.SUCCEEDED.value
            assert final_job.attempts == 2

    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
