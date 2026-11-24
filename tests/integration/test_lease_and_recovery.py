"""Integration tests for lease management, fencing tokens, and crash recovery."""

import uuid
from datetime import UTC, datetime, timedelta

from app.db.models import AttemptStatus, Job, JobAttempt, JobStatus
from app.queue.lease import renew_lease
from app.queue.recovery import recover_expired_leases
from app.queue.state import complete_job_success
from tests.conftest import TestingSessionLocal


def test_lease_renewal_extends_expiration() -> None:
    """Verify that a worker can renew an active lease."""
    job_id = uuid.uuid4()
    past_time = datetime.now(UTC) + timedelta(seconds=10)

    with TestingSessionLocal() as db:
        job = Job(
            id=job_id,
            job_type="test_job",
            payload={},
            status=JobStatus.RUNNING.value,
            locked_by="worker-alpha",
            lease_token=1,
            lease_expires_at=past_time,
        )
        db.add(job)
        db.commit()

        # Renew lease for 60 seconds
        renewed = renew_lease(
            db=db,
            job_id=job_id,
            worker_id="worker-alpha",
            lease_token=1,
            lease_duration_seconds=60,
        )
        assert renewed is True

        refreshed = db.get(Job, job_id)
        assert refreshed is not None
        assert refreshed.lease_expires_at is not None
        assert refreshed.lease_expires_at > past_time


def test_stale_worker_fencing_token_protection() -> None:
    """Verify that a stale worker with an outdated lease token cannot complete a job."""
    job_id = uuid.uuid4()
    attempt_id = uuid.uuid4()

    with TestingSessionLocal() as db:
        # Job has progressed to lease_token=2 (e.g. recovered and reclaimed by worker-2)
        job = Job(
            id=job_id,
            job_type="test_job",
            payload={},
            status=JobStatus.RUNNING.value,
            locked_by="worker-2",
            lease_token=2,
            attempts=2,
        )
        attempt = JobAttempt(
            id=attempt_id,
            job_id=job_id,
            attempt_number=1,
            worker_id="worker-1",
            status=AttemptStatus.RUNNING.value,
            lease_token=1,
        )
        db.add_all([job, attempt])
        db.commit()

        # Worker-1 tries to complete the job with stale token 1
        success = complete_job_success(
            db=db,
            job_id=job_id,
            attempt_id=attempt_id,
            worker_id="worker-1",
            lease_token=1,
            result={"status": "stale_data"},
        )
        assert success is False, "Stale worker was incorrectly permitted to complete job!"

        # Verify job is still locked by worker-2 and status is still RUNNING
        refreshed = db.get(Job, job_id)
        assert refreshed is not None
        assert refreshed.status == JobStatus.RUNNING.value
        assert refreshed.locked_by == "worker-2"
        assert refreshed.lease_token == 2


def test_crash_recovery_marks_abandoned_and_retries() -> None:
    """Verify that a crashed worker's expired job is marked ABANDONED and re-enqueued for retry."""
    job_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    past_time = datetime.now(UTC) - timedelta(seconds=10)  # Expired!

    with TestingSessionLocal() as db:
        job = Job(
            id=job_id,
            job_type="test_job",
            payload={},
            status=JobStatus.RUNNING.value,
            locked_by="crashed-worker",
            lease_token=1,
            attempts=1,
            max_attempts=3,
            lease_expires_at=past_time,
        )
        attempt = JobAttempt(
            id=attempt_id,
            job_id=job_id,
            attempt_number=1,
            worker_id="crashed-worker",
            status=AttemptStatus.RUNNING.value,
            lease_token=1,
        )
        db.add_all([job, attempt])
        db.commit()

        # Run recovery
        recovered = recover_expired_leases(db, base_retry_delay_seconds=1.0)
        assert job_id in recovered

        # Check job status
        refreshed_job = db.get(Job, job_id)
        assert refreshed_job is not None
        assert refreshed_job.status == JobStatus.RETRYING.value
        assert refreshed_job.locked_by is None
        assert refreshed_job.lease_expires_at is None
        assert refreshed_job.last_error is not None
        assert "abandoned" in refreshed_job.last_error.lower()

        # Check attempt was marked ABANDONED
        refreshed_attempt = db.get(JobAttempt, attempt_id)
        assert refreshed_attempt is not None
        assert refreshed_attempt.status == AttemptStatus.ABANDONED.value
        assert refreshed_attempt.finished_at is not None


def test_crash_recovery_marks_failed_when_max_attempts_exhausted() -> None:
    """Verify that an abandoned job that has reached max_attempts transitions to FAILED."""
    job_id = uuid.uuid4()
    past_time = datetime.now(UTC) - timedelta(seconds=10)

    with TestingSessionLocal() as db:
        job = Job(
            id=job_id,
            job_type="test_job",
            payload={},
            status=JobStatus.RUNNING.value,
            locked_by="crashed-worker-exhausted",
            lease_token=5,
            attempts=5,
            max_attempts=5,  # Exhausted!
            lease_expires_at=past_time,
        )
        attempt = JobAttempt(
            job_id=job_id,
            attempt_number=5,
            worker_id="crashed-worker-exhausted",
            status=AttemptStatus.RUNNING.value,
            lease_token=5,
        )
        db.add_all([job, attempt])
        db.commit()

        recovered = recover_expired_leases(db)
        assert job_id in recovered

        refreshed = db.get(Job, job_id)
        assert refreshed is not None
        assert refreshed.status == JobStatus.FAILED.value
        assert refreshed.last_error is not None
        assert "exceeded max attempts" in refreshed.last_error.lower()
