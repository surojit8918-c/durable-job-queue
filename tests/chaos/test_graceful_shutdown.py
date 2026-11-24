"""Chaos test: graceful worker shutdown upon receiving OS termination signal."""

import os
import signal
import subprocess
import sys
import textwrap
import time
import uuid

from app.core.config import settings
from app.db.models import Job, JobStatus
from tests.conftest import TestingSessionLocal


def test_graceful_shutdown_finishes_in_flight_job_and_stops_claiming() -> None:
    """Verify that upon receiving a termination signal (SIGTERM/SIGINT/SIGBREAK):

    1. The worker stops claiming any new jobs.
    2. The in-flight job finishes and commits SUCCEEDED to PostgreSQL within the grace period.
    3. Any subsequent queued jobs remain in QUEUED state without being claimed.
    4. The worker process exits cleanly (return code 0).
    """
    job1_id = uuid.uuid4()
    job2_id = uuid.uuid4()

    # 1. Enqueue two jobs: Job 1 is running, Job 2 is waiting behind it
    with TestingSessionLocal() as db:
        job1 = Job(
            id=job1_id,
            job_type="slow_graceful_job",
            payload={"duration": 1.2, "name": "in-flight-task"},
            status=JobStatus.QUEUED.value,
        )
        job2 = Job(
            id=job2_id,
            job_type="slow_graceful_job",
            payload={"duration": 0.1, "name": "waiting-task"},
            status=JobStatus.QUEUED.value,
        )
        db.add_all([job1, job2])
        db.commit()

    # 2. Spawn worker in an isolated OS subprocess
    worker_env = os.environ.copy()
    worker_env["DATABASE_URL"] = settings.TEST_DATABASE_URL
    worker_env["LEASE_DURATION_SECONDS"] = "30"
    worker_env["LEASE_RENEWAL_INTERVAL_SECONDS"] = "5"
    worker_env["WORKER_POLL_INTERVAL"] = "0.05"

    worker_code = textwrap.dedent("""
    import time
    from app.handlers.registry import register_handler
    from app.workers.worker import Worker

    @register_handler("slow_graceful_job")
    def handle_slow_graceful(payload):
        duration = payload.get("duration", 1.0)
        time.sleep(duration)
        return {"status": "completed_gracefully", "duration": duration}

    worker = Worker(worker_id="graceful-worker", poll_interval=0.05, lease_duration=30)
    worker.run()
    """)

    creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0

    proc = subprocess.Popen(
        [sys.executable, "-c", worker_code],
        env=worker_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=creation_flags,
    )

    try:
        # Wait until worker has claimed Job 1
        claimed = False
        for _ in range(40):
            time.sleep(0.1)
            with TestingSessionLocal() as db:
                db_job1 = db.get(Job, job1_id)
                if db_job1 and db_job1.status == JobStatus.RUNNING.value:
                    claimed = True
                    break

        assert claimed, "Worker failed to claim in-flight Job 1 within timeout"

        # 3. Send termination signal while Job 1 is executing
        if sys.platform == "win32":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.send_signal(signal.SIGTERM)

        # 4. Wait for worker to exit cleanly
        stdout, stderr = proc.communicate(timeout=8)
        assert proc.returncode == 0, (
            f"Worker exited with non-zero code {proc.returncode}. Stderr: {stderr.decode()}"
        )

        # 5. Database state verification:
        with TestingSessionLocal() as db:
            refreshed_job1 = db.get(Job, job1_id)
            assert refreshed_job1 is not None
            # Job 1 was in-flight and MUST have completed to SUCCEEDED
            assert refreshed_job1.status == JobStatus.SUCCEEDED.value, (
                f"In-flight job was not completed gracefully! Status is {refreshed_job1.status}"
            )
            assert refreshed_job1.result is not None
            assert refreshed_job1.result["status"] == "completed_gracefully"

            # Job 2 was QUEUED and MUST NOT have been claimed by the shutting down worker
            refreshed_job2 = db.get(Job, job2_id)
            assert refreshed_job2 is not None
            assert refreshed_job2.status == JobStatus.QUEUED.value, (
                f"Worker improperly claimed Job 2 after shutdown signal! Status is {refreshed_job2.status}"
            )
            assert refreshed_job2.attempts == 0
            assert refreshed_job2.locked_by is None

    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
