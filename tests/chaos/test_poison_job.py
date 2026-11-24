"""Chaos tests for poison job handling across multiple simulated worker crashes."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.models import AttemptStatus, Job, JobAttempt, JobStatus
from app.queue.claiming import claim_next_job
from app.queue.recovery import recover_expired_leases
from tests.conftest import TestingSessionLocal


def test_poison_job_lifecycle_to_terminal_failure() -> None:
    """Verify that a poison job that repeatedly crashes workers exhausts retries and becomes FAILED."""
    job_id = uuid.uuid4()
    max_attempts = 5

    # 1. Enqueue poison job
    with TestingSessionLocal() as db:
        job = Job(
            id=job_id,
            job_type="test_job",
            payload={"poison": True},
            status=JobStatus.QUEUED.value,
            max_attempts=max_attempts,
        )
        db.add(job)
        db.commit()

    # 2. Simulate 5 consecutive crashes and recoveries
    for attempt_idx in range(1, max_attempts + 1):
        with TestingSessionLocal() as db:
            # Worker claims job
            claimed = claim_next_job(
                db=db,
                worker_id=f"doomed-worker-{attempt_idx}",
                lease_duration_seconds=1,  # Short lease
            )
            assert claimed is not None
            c_job, c_attempt = claimed
            assert c_job.id == job_id
            assert c_job.attempts == attempt_idx
            assert c_job.status == JobStatus.RUNNING.value

            # Simulate worker abrupt crash: worker dies without updating DB
            # Force lease expiration
            c_job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=5)
            db.commit()

            # Recovery process discovers expired lease
            recovered = recover_expired_leases(db, base_retry_delay_seconds=0.01)
            assert job_id in recovered

            # Check status after recovery
            refreshed = db.get(Job, job_id)
            assert refreshed is not None
            if attempt_idx < max_attempts:
                assert refreshed.status == JobStatus.RETRYING.value
                # Make immediately available for next iteration in test
                refreshed.available_at = datetime.now(UTC) - timedelta(seconds=1)
                db.commit()
            else:
                assert refreshed.status == JobStatus.FAILED.value

    # 3. Verify terminal failure state and history preservation
    with TestingSessionLocal() as db:
        final_job = db.get(Job, job_id)
        assert final_job is not None
        assert final_job.status == JobStatus.FAILED.value
        assert final_job.attempts == 5

        # Ensure it cannot be reclaimed automatically
        claim_after_fail = claim_next_job(db, worker_id="another-worker")
        assert claim_after_fail is None

        # Verify all 5 historical attempts are recorded as ABANDONED
        attempts = db.scalars(
            select(JobAttempt)
            .where(JobAttempt.job_id == job_id)
            .order_by(JobAttempt.attempt_number.asc())
        ).all()
        assert len(attempts) == 5
        for att in attempts:
            assert att.status == AttemptStatus.ABANDONED.value
            assert att.error_message is not None
            assert "crashed" in att.error_message.lower() or "presumed" in att.error_message.lower()

    # 4. Operator manual retry starts new cycle while keeping all 5 history records
    with TestingSessionLocal() as db:
        retry_job = db.get(Job, job_id)
        assert retry_job is not None
        retry_job.status = JobStatus.QUEUED.value
        retry_job.attempts = 0
        retry_job.locked_by = None
        retry_job.lease_expires_at = None
        db.commit()

        # Claim in new retry cycle
        new_claim = claim_next_job(db, worker_id="fresh-worker", lease_duration_seconds=30)
        assert new_claim is not None
        assert new_claim[0].id == job_id
        assert new_claim[0].attempts == 1

        # Verify historical attempts now count 6 (5 old ABANDONED + 1 new RUNNING)
        total_attempts = db.scalars(select(JobAttempt).where(JobAttempt.job_id == job_id)).all()
        assert len(total_attempts) == 6
