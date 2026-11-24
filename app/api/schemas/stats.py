"""Pydantic schemas for operational statistics."""

from pydantic import BaseModel


class StatsResponse(BaseModel):
    """Operational monitoring indicators calculated from PostgreSQL."""

    total_jobs: int
    queued_jobs: int
    running_jobs: int
    retrying_jobs: int
    succeeded_jobs: int
    failed_jobs: int
    expired_leases: int
    oldest_queued_job_age_seconds: float
