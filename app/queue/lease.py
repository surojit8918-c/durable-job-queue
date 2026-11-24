"""Lease renewal and management for active jobs."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import CursorResult, func, update
from sqlalchemy.orm import Session

from app.core.logging import logger
from app.db.models import Job, JobStatus


def renew_lease(
    db: Session,
    job_id: uuid.UUID,
    worker_id: str,
    lease_token: int,
    lease_duration_seconds: int = 60,
) -> bool:
    """Renew the lease on a currently executing job.

    Verifies fencing token, locked_by, and status before extending lease_expires_at.
    Returns True if lease was successfully renewed; False if lost or stale.
    """
    new_expires_at = datetime.now(UTC) + timedelta(seconds=lease_duration_seconds)

    stmt = (
        update(Job)
        .where(
            Job.id == job_id,
            Job.locked_by == worker_id,
            Job.lease_token == lease_token,
            Job.status == JobStatus.RUNNING.value,
        )
        .values(
            lease_expires_at=new_expires_at,
            updated_at=func.now(),
        )
    )

    result = cast(CursorResult[Any], db.execute(stmt))
    db.commit()

    if result.rowcount == 1:
        logger.debug(
            "Lease renewed successfully",
            extra={
                "job_id": str(job_id),
                "worker_id": worker_id,
                "lease_token": lease_token,
                "event": "lease_renewed",
            },
        )
        return True

    logger.warning(
        "Failed to renew lease - ownership lost or stale fencing token",
        extra={
            "job_id": str(job_id),
            "worker_id": worker_id,
            "lease_token": lease_token,
            "event": "lease_renewal_failed",
        },
    )
    return False
