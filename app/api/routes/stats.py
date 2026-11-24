"""Operational stats routes."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas.stats import StatsResponse
from app.db.models import Job, JobStatus
from app.db.session import get_db

router = APIRouter(tags=["Monitoring"])


@router.get(
    "/stats",
    response_model=StatsResponse,
    summary="Get operational statistics",
    description="Calculates system health indicators from PostgreSQL including queue counts and lease metrics.",
)
def get_stats(db: Annotated[Session, Depends(get_db)]) -> StatsResponse:
    """Compute operational metrics directly from PostgreSQL."""
    total_jobs = db.scalar(select(func.count(Job.id))) or 0

    queued_jobs = (
        db.scalar(select(func.count(Job.id)).where(Job.status == JobStatus.QUEUED.value)) or 0
    )
    running_jobs = (
        db.scalar(select(func.count(Job.id)).where(Job.status == JobStatus.RUNNING.value)) or 0
    )
    retrying_jobs = (
        db.scalar(select(func.count(Job.id)).where(Job.status == JobStatus.RETRYING.value)) or 0
    )
    succeeded_jobs = (
        db.scalar(select(func.count(Job.id)).where(Job.status == JobStatus.SUCCEEDED.value)) or 0
    )
    failed_jobs = (
        db.scalar(select(func.count(Job.id)).where(Job.status == JobStatus.FAILED.value)) or 0
    )

    expired_leases = (
        db.scalar(
            select(func.count(Job.id)).where(
                Job.status == JobStatus.RUNNING.value,
                Job.lease_expires_at <= func.now(),
            )
        )
        or 0
    )

    # Calculate age in seconds of the oldest eligible queued job
    oldest_age = db.scalar(
        select(
            func.coalesce(
                func.extract("epoch", func.now() - func.min(Job.created_at)),
                0.0,
            )
        ).where(
            Job.status == JobStatus.QUEUED.value,
            Job.available_at <= func.now(),
        )
    )
    oldest_queued_job_age_seconds = float(oldest_age or 0.0)

    return StatsResponse(
        total_jobs=total_jobs,
        queued_jobs=queued_jobs,
        running_jobs=running_jobs,
        retrying_jobs=retrying_jobs,
        succeeded_jobs=succeeded_jobs,
        failed_jobs=failed_jobs,
        expired_leases=expired_leases,
        oldest_queued_job_age_seconds=max(0.0, oldest_queued_job_age_seconds),
    )
