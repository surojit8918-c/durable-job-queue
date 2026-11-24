"""Atomic job claiming implementation using PostgreSQL SELECT FOR UPDATE SKIP LOCKED."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import logger
from app.db.models import AttemptStatus, Job, JobAttempt, JobStatus


def claim_next_job(
    db: Session,
    worker_id: str,
    lease_duration_seconds: int = 60,
) -> tuple[Job, JobAttempt] | None:
    """Atomically claim the highest-priority eligible job for execution.

    Uses `FOR UPDATE SKIP LOCKED` inside a short transaction to prevent double claiming.
    Updates ownership, increments fencing lease_token, creates an attempt record,
    and commits the transaction immediately so handlers execute outside the claim lock.
    """
    now_utc = datetime.now(UTC)
    lease_expires = now_utc + timedelta(seconds=lease_duration_seconds)

    # 1. Atomically select the highest-priority eligible job without waiting
    stmt = (
        select(Job)
        .where(
            Job.status.in_([JobStatus.QUEUED.value, JobStatus.RETRYING.value]),
            Job.available_at <= func.now(),
        )
        .order_by(Job.priority.desc(), Job.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )

    job = db.scalar(stmt)
    if job is None:
        db.rollback()
        return None

    # 2. Establish ownership and increment fencing token
    job.status = JobStatus.RUNNING.value
    job.locked_by = worker_id
    job.lease_token += 1
    job.attempts += 1
    job.lease_expires_at = lease_expires
    job.updated_at = func.now()

    # 3. Create historical attempt record
    attempt = JobAttempt(
        job_id=job.id,
        attempt_number=job.attempts,
        worker_id=worker_id,
        status=AttemptStatus.RUNNING.value,
        started_at=func.now(),
        lease_token=job.lease_token,
    )
    db.add(attempt)

    # 4. Commit immediately: database lock is released before handler runs
    db.commit()
    db.refresh(job)
    db.refresh(attempt)

    logger.info(
        "Job claimed successfully",
        extra={
            "job_id": str(job.id),
            "attempt_id": str(attempt.id),
            "worker_id": worker_id,
            "lease_token": job.lease_token,
            "attempt_number": attempt.attempt_number,
            "event": "job_claimed",
        },
    )
    return job, attempt
