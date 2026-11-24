"""Chaos test: worker crash immediately after external partner API succeeds but before DB commit."""

import os
import subprocess
import sys
import textwrap
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.db.models import AttemptStatus, Job, JobAttempt, JobStatus
from app.handlers.partner_api import mock_partner
from app.queue.recovery import recover_expired_leases
from app.workers.worker import Worker
from tests.conftest import TestingSessionLocal


def test_worker_crash_post_partner_success_prevents_duplicate_side_effect() -> None:
    """Verify that if a worker crashes after the partner API succeeds but before DB commit:

    1. The job remains durable and is recovered after lease expiration.
    2. Attempt 1 is preserved and recorded as ABANDONED.
    3. A new worker reclaims and re-runs the job using the same stable idempotency key.
    4. The partner API deduplicates the call and PREVENTS duplicate business side effects.
    5. The final job status is recorded as SUCCEEDED.
    """
    job_id = uuid.uuid4()
    lease_duration = 3
    customer_id = 98765

    # 1. Clear partner state
    mock_partner.clear()

    # 2. Enqueue partner API job
    with TestingSessionLocal() as db:
        job = Job(
            id=job_id,
            job_type="partner_api_call_crashing",
            payload={"customer_id": customer_id, "operation": "charge_account"},
            status=JobStatus.QUEUED.value,
            max_attempts=3,
        )
        db.add(job)
        db.commit()

    # 3. Spawn a worker subprocess running a custom crashing handler
    # The handler calls the real partner API (executing the external side-effect)
    # and then immediately calls os._exit(1) to simulate a hard crash (SIGKILL/OOM)
    # before the worker loop can execute complete_job_success() in PostgreSQL.
    worker_env = os.environ.copy()
    worker_env["DATABASE_URL"] = settings.TEST_DATABASE_URL
    worker_env["LEASE_DURATION_SECONDS"] = str(lease_duration)
    worker_env["LEASE_RENEWAL_INTERVAL_SECONDS"] = "1"
    worker_env["WORKER_POLL_INTERVAL"] = "0.1"

    worker_code = textwrap.dedent(f"""
    import os
    import sys
    from app.handlers.partner_api import mock_partner
    from app.handlers.registry import register_handler
    from app.workers.worker import Worker

    @register_handler("partner_api_call_crashing")
    def handle_crashing_partner(payload):
        stable_key = f"partner-{{payload.get('customer_id', 'anon')}}"
        # 1. Partner API call succeeds (charging the customer / creating resource)
        result = mock_partner.call(endpoint="charge_account", payload=payload, idempotency_key=stable_key)
        # 2. Hard crash IMMEDIATELY before returning to the worker to commit to PostgreSQL
        os._exit(42)

    worker = Worker(worker_id="crash-worker-post-api", lease_duration={lease_duration}, poll_interval=0.1)
    worker.run()
    """)

    proc = subprocess.Popen(
        [sys.executable, "-c", worker_code],
        env=worker_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # 4. Wait for subprocess to crash
    exit_code = proc.wait(timeout=10)
    assert exit_code == 42, f"Subprocess exited with unexpected code: {exit_code}"

    # Verify that the partner API did indeed execute the side effect once before crash
    assert len(mock_partner.received_operations) == 1
    assert len(mock_partner.executed_business_operations) == 1
    assert (
        mock_partner.executed_business_operations[0]["idempotency_key"] == f"partner-{customer_id}"
    )

    # 5. Verify database state immediately after crash: job is still RUNNING
    with TestingSessionLocal() as db:
        crashed_job = db.get(Job, job_id)
        assert crashed_job is not None
        assert crashed_job.status == JobStatus.RUNNING.value
        assert crashed_job.locked_by == "crash-worker-post-api"
        assert crashed_job.result is None  # Never updated in DB!

    # 6. Wait for lease to expire
    time.sleep(lease_duration + 0.5)

    # 7. Recovery process discovers expired lease
    with TestingSessionLocal() as db:
        recovered = recover_expired_leases(db, base_retry_delay_seconds=0.1)
        assert job_id in recovered

        refreshed = db.get(Job, job_id)
        assert refreshed is not None
        assert refreshed.status == JobStatus.RETRYING.value
        assert refreshed.locked_by is None

        # Check Attempt 1 is recorded as ABANDONED
        attempt1 = db.scalar(
            select(JobAttempt).where(JobAttempt.job_id == job_id, JobAttempt.attempt_number == 1)
        )
        assert attempt1 is not None
        assert attempt1.status == AttemptStatus.ABANDONED.value
        assert attempt1.worker_id == "crash-worker-post-api"

        # Make available immediately for Worker 2
        refreshed.available_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()

    # 8. Register normal partner handler under the job type for Worker 2 replay
    from app.handlers.registry import register_handler

    @register_handler("partner_api_call_crashing")
    def handle_replay_partner(payload: dict[str, Any]) -> dict[str, Any]:
        stable_key = f"partner-{payload.get('customer_id', 'anon')}"
        return mock_partner.call(
            endpoint="charge_account", payload=payload, idempotency_key=stable_key
        )

    # 9. Clean worker reclaims and completes the job
    worker2 = Worker(
        worker_id="worker-2-replay",
        lease_duration=30,
        session_factory=TestingSessionLocal,
    )
    with TestingSessionLocal() as db:
        processed = worker2.process_one_job(db)
        assert processed is True

    # 10. Deep verification:
    # A. Partner API received 2 HTTP calls
    assert len(mock_partner.received_operations) == 2, (
        "Partner should have received 2 total requests"
    )
    # B. Partner API executed ONLY 1 business operation (duplicate side-effect was prevented!)
    assert len(mock_partner.executed_business_operations) == 1, (
        f"DUPLICATE BUSINESS SIDE-EFFECT DETECTED! Expected 1, got {len(mock_partner.executed_business_operations)}"
    )
    # C. Both requests used the identical idempotency key
    key1 = mock_partner.received_operations[0]["idempotency_key"]
    key2 = mock_partner.received_operations[1]["idempotency_key"]
    assert key1 == key2 == f"partner-{customer_id}"

    # D. Database state is now SUCCEEDED with complete attempt history
    with TestingSessionLocal() as db:
        final_job = db.get(Job, job_id)
        assert final_job is not None
        assert final_job.status == JobStatus.SUCCEEDED.value
        assert final_job.attempts == 2
        assert final_job.result is not None
        assert final_job.result["partner_status"] == "success"

        # Verify historical attempts: Attempt 1 is ABANDONED, Attempt 2 is SUCCEEDED
        attempts = db.scalars(
            select(JobAttempt)
            .where(JobAttempt.job_id == job_id)
            .order_by(JobAttempt.attempt_number.asc())
        ).all()
        assert len(attempts) == 2
        assert attempts[0].status == AttemptStatus.ABANDONED.value
        assert attempts[0].worker_id == "crash-worker-post-api"
        assert attempts[1].status == AttemptStatus.SUCCEEDED.value
        assert attempts[1].worker_id == "worker-2-replay"
