"""Pydantic schemas for Job endpoints."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class JobCreate(BaseModel):
    """Schema for submitting a new job."""

    job_type: str = Field(..., description="Registered job type identifier", min_length=1)
    payload: dict[str, Any] = Field(..., description="Job execution input payload")
    priority: int = Field(default=0, description="Priority integer (higher executed first)")
    run_at: datetime | None = Field(
        default=None,
        description="Scheduled future execution time in ISO 8601 format",
    )


class JobResponse(BaseModel):
    """Schema returned immediately upon job creation or duplicate submission."""

    job_id: uuid.UUID
    status: str

    model_config = ConfigDict(from_attributes=True)


class JobDetailResponse(BaseModel):
    """Schema for detailed job inspection."""

    job_id: uuid.UUID
    job_type: str
    status: str
    priority: int
    attempts: int
    max_attempts: int
    result: dict[str, Any] | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class JobRetryResponse(BaseModel):
    """Schema for operator manual retry response."""

    job_id: uuid.UUID
    status: str
    message: str
