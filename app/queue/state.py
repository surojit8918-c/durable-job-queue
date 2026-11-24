"""State finalization with fencing token validation."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, func, update
from sqlalchemy.orm import Session

from app.core.logging import logger
from app.db.models import AttemptStatus, Job, JobAttempt, JobStatus
from app.queue.retry import calculate_retry_delay, classify_error


def complete_job_success(
    db: Session,
    job_id: uuid.UUID,
    attempt_id: uuid.UUID,
    worker_id: str,
    lease_token: int,
    result: dict[str, Any],
) -> bool:
    """Finalize a successfully executed job.

    Validates that worker_id and lease_token match current ownership.
    Returns True if update succeeded, False if fencing token was violated.
    """
    job_stmt = (
        update(Job)
        .where(
            Job.id == job_id,
            Job.locked_by == worker_id,
            Job.lease_token == lease_token,
            Job.status == JobStatus.RUNNING.value,
        )
        .values(
            status=JobStatus.SUCCEEDED.value,
            result=result,
            last_error=None,
            locked_by=None,
            lease_expires_at=None,
            updated_at=func.now(),
        )
    )

    res = cast(CursorResult[Any], db.execute(job_stmt))
    if res.rowcount == 0:
        db.rollback()
        logger.warning(
            "Fencing violation on job completion - discarded stale result",
            extra={
                "job_id": str(job_id),
                "worker_id": worker_id,
                "lease_token": lease_token,
                "event": "fencing_violation",
            },
        )
        return False

    # Update attempt record
    attempt_stmt = (
        update(JobAttempt)
        .where(JobAttempt.id == attempt_id)
        .values(
            status=AttemptStatus.SUCCEEDED.value,
            finished_at=func.now(),
            result=result,
        )
    )
    db.execute(attempt_stmt)
    db.commit()

    logger.info(
        "Job completed successfully",
        extra={
            "job_id": str(job_id),
            "attempt_id": str(attempt_id),
            "worker_id": worker_id,
            "event": "job_succeeded",
        },
    )
    return True


def complete_job_failure(
    db: Session,
    job_id: uuid.UUID,
    attempt_id: uuid.UUID,
    worker_id: str,
    lease_token: int,
    error: Exception,
    base_retry_delay_seconds: float = 2.0,
    max_retry_delay_seconds: float = 60.0,
) -> bool:
    """Finalize a failed job execution attempt with retry scheduling or permanent failure.

    Validates fencing token before committing state transition.
    """
    # 1. Inspect current job to determine retry eligibility
    job = db.get(Job, job_id)
    if job is None or job.locked_by != worker_id or job.lease_token != lease_token:
        db.rollback()
        logger.warning(
            "Fencing violation on job failure - discarded stale failure update",
            extra={
                "job_id": str(job_id),
                "worker_id": worker_id,
                "lease_token": lease_token,
                "event": "fencing_violation",
            },
        )
        return False

    is_retryable, retry_after = classify_error(error)
    error_msg = f"{type(error).__name__}: {error}"

    # 2. Check if eligible for retry
    if is_retryable and job.attempts < job.max_attempts:
        delay = calculate_retry_delay(
            attempt=job.attempts,
            base_delay_seconds=base_retry_delay_seconds,
            max_delay_seconds=max_retry_delay_seconds,
            retry_after=retry_after,
        )
        new_status = JobStatus.RETRYING.value
        available_at = datetime.now(UTC) + timedelta(seconds=delay)

        job_stmt = (
            update(Job)
            .where(
                Job.id == job_id,
                Job.locked_by == worker_id,
                Job.lease_token == lease_token,
                Job.status == JobStatus.RUNNING.value,
            )
            .values(
                status=new_status,
                available_at=available_at,
                last_error=error_msg,
                locked_by=None,
                lease_expires_at=None,
                updated_at=func.now(),
            )
        )
        logger.info(
            "Scheduled retry for job",
            extra={
                "job_id": str(job_id),
                "attempts": job.attempts,
                "delay_seconds": delay,
                "error": error_msg,
                "event": "retry_scheduled",
            },
        )
    else:
        new_status = JobStatus.FAILED.value
        job_stmt = (
            update(Job)
            .where(
                Job.id == job_id,
                Job.locked_by == worker_id,
                Job.lease_token == lease_token,
                Job.status == JobStatus.RUNNING.value,
            )
            .values(
                status=new_status,
                last_error=error_msg,
                locked_by=None,
                lease_expires_at=None,
                updated_at=func.now(),
            )
        )
        logger.warning(
            "Job transitioned to terminal FAILED status",
            extra={
                "job_id": str(job_id),
                "attempts": job.attempts,
                "is_retryable": is_retryable,
                "error": error_msg,
                "event": "job_permanently_failed",
            },
        )

    res = cast(CursorResult[Any], db.execute(job_stmt))
    if res.rowcount == 0:
        db.rollback()
        return False

    # 3. Update attempt record
    attempt_stmt = (
        update(JobAttempt)
        .where(JobAttempt.id == attempt_id)
        .values(
            status=AttemptStatus.FAILED.value,
            finished_at=func.now(),
            error_message=error_msg,
        )
    )
    db.execute(attempt_stmt)
    db.commit()
    return True
