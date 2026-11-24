"""Crash recovery logic for abandoned or expired job leases."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import logger
from app.db.models import AttemptStatus, Job, JobAttempt, JobStatus
from app.queue.retry import calculate_retry_delay


def recover_expired_leases(
    db: Session,
    base_retry_delay_seconds: float = 2.0,
    max_retry_delay_seconds: float = 60.0,
) -> list[uuid.UUID]:
    """Scan for RUNNING jobs whose leases have expired and recover them.

    1. Marks the active attempt record as ABANDONED.
    2. If attempts < max_attempts: re-enqueues job as RETRYING with exponential backoff.
    3. If attempts >= max_attempts: marks job as FAILED.
    """
    stmt = (
        select(Job)
        .where(
            Job.status == JobStatus.RUNNING.value,
            Job.lease_expires_at <= func.now(),
        )
        .with_for_update(skip_locked=True)
    )

    expired_jobs = db.scalars(stmt).all()
    recovered_job_ids: list[uuid.UUID] = []

    for job in expired_jobs:
        # 1. Mark latest RUNNING attempt as ABANDONED
        attempt_stmt = (
            select(JobAttempt)
            .where(
                JobAttempt.job_id == job.id,
                JobAttempt.status == AttemptStatus.RUNNING.value,
            )
            .order_by(JobAttempt.attempt_number.desc())
            .limit(1)
        )
        attempt = db.scalar(attempt_stmt)
        if attempt is not None:
            attempt.status = AttemptStatus.ABANDONED.value
            attempt.finished_at = func.now()
            attempt.error_message = f"Worker '{job.locked_by}' lease expired at {job.lease_expires_at}; presumed crashed."

        # 2. Check retry capacity
        if job.attempts < job.max_attempts:
            delay = calculate_retry_delay(
                attempt=job.attempts,
                base_delay_seconds=base_retry_delay_seconds,
                max_delay_seconds=max_retry_delay_seconds,
            )
            job.status = JobStatus.RETRYING.value
            job.available_at = datetime.now(UTC) + timedelta(seconds=delay)
            job.last_error = f"Attempt {job.attempts} abandoned due to expired lease by worker '{job.locked_by}'."
            job.locked_by = None
            job.lease_expires_at = None
            job.updated_at = func.now()

            logger.info(
                "Recovered abandoned job for retry",
                extra={
                    "job_id": str(job.id),
                    "attempts": job.attempts,
                    "max_attempts": job.max_attempts,
                    "retry_delay": delay,
                    "event": "job_recovered_retrying",
                },
            )
        else:
            job.status = JobStatus.FAILED.value
            job.last_error = f"Job abandoned by worker '{job.locked_by}' and exceeded max attempts ({job.max_attempts})."
            job.locked_by = None
            job.lease_expires_at = None
            job.updated_at = func.now()

            logger.warning(
                "Recovered abandoned job marked as permanently FAILED (max attempts reached)",
                extra={
                    "job_id": str(job.id),
                    "attempts": job.attempts,
                    "max_attempts": job.max_attempts,
                    "event": "job_recovered_failed",
                },
            )

        recovered_job_ids.append(job.id)

    db.commit()
    return recovered_job_ids
