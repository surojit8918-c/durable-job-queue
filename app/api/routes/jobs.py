"""Job API routes."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.schemas.jobs import JobCreate, JobDetailResponse, JobResponse, JobRetryResponse
from app.core.logging import logger
from app.db.models import IdempotencyKey, Job, JobStatus
from app.db.session import get_db
from app.handlers.registry import is_valid_job_type
from app.queue.idempotency import compute_request_hash

router = APIRouter(tags=["Jobs"])


@router.post(
    "/jobs",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a background job",
    description="Submits a background job with optional priority, scheduling, and idempotency key.",
)
def create_job(
    job_in: JobCreate,
    db: Annotated[Session, Depends(get_db)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JobResponse:
    """Accept and durably persist a job in PostgreSQL."""
    # Validate job type against registered handlers
    if not is_valid_job_type(job_in.job_type):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Job type '{job_in.job_type}' is not registered.",
        )

    # Handle Idempotency Key if supplied
    if idempotency_key is not None and idempotency_key.strip():
        idempotency_key = idempotency_key.strip()
        req_hash = compute_request_hash(
            job_type=job_in.job_type,
            payload=job_in.payload,
            priority=job_in.priority,
            run_at=job_in.run_at,
        )

        existing_key = db.scalar(
            select(IdempotencyKey).where(IdempotencyKey.idempotency_key == idempotency_key)
        )
        if existing_key is not None:
            if existing_key.request_hash == req_hash:
                existing_job = db.get(Job, existing_key.job_id)
                if existing_job is not None:
                    logger.info(
                        "Duplicate job submission matched existing idempotency key",
                        extra={"job_id": str(existing_job.id), "event": "idempotency_match"},
                    )
                    return JobResponse(job_id=existing_job.id, status=existing_job.status)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency key conflict: request content does not match existing record.",
            )

        # Create new job and idempotency record transactionally
        new_job = Job(
            job_type=job_in.job_type,
            payload=job_in.payload,
            priority=job_in.priority,
            run_at=job_in.run_at or func.now(),
            available_at=job_in.run_at or func.now(),
            status=JobStatus.QUEUED.value,
        )
        db.add(new_job)
        db.flush()

        idempotency_record = IdempotencyKey(
            idempotency_key=idempotency_key,
            request_hash=req_hash,
            job_id=new_job.id,
        )
        db.add(idempotency_record)

        try:
            db.commit()
            db.refresh(new_job)
        except IntegrityError:
            db.rollback()
            # Handle race condition where concurrent request inserted same key
            retry_key = db.scalar(
                select(IdempotencyKey).where(IdempotencyKey.idempotency_key == idempotency_key)
            )
            if retry_key and retry_key.request_hash == req_hash:
                matched_job = db.get(Job, retry_key.job_id)
                if matched_job is not None:
                    return JobResponse(job_id=matched_job.id, status=matched_job.status)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Idempotency key conflict during concurrent submission.",
            ) from None

        logger.info(
            "Job submitted and persisted",
            extra={
                "job_id": str(new_job.id),
                "job_type": new_job.job_type,
                "status": new_job.status,
                "event": "job_submitted",
            },
        )
        return JobResponse(job_id=new_job.id, status=new_job.status)

    # Submission without idempotency key
    new_job = Job(
        job_type=job_in.job_type,
        payload=job_in.payload,
        priority=job_in.priority,
        run_at=job_in.run_at or func.now(),
        available_at=job_in.run_at or func.now(),
        status=JobStatus.QUEUED.value,
    )
    db.add(new_job)
    db.commit()
    db.refresh(new_job)

    logger.info(
        "Job submitted and persisted",
        extra={
            "job_id": str(new_job.id),
            "job_type": new_job.job_type,
            "status": new_job.status,
            "event": "job_submitted",
        },
    )
    return JobResponse(job_id=new_job.id, status=new_job.status)


@router.get(
    "/jobs/{job_id}",
    response_model=JobDetailResponse,
    summary="Retrieve job details",
    description="Retrieves current execution status, attempts count, and result/error details.",
)
def get_job(
    job_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
) -> JobDetailResponse:
    """Retrieve detailed information about a job."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job with ID '{job_id}' not found.",
        )
    return JobDetailResponse(
        job_id=job.id,
        job_type=job.job_type,
        status=job.status,
        priority=job.priority,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        result=job.result,
        last_error=job.last_error,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.post(
    "/jobs/{job_id}/retry",
    response_model=JobRetryResponse,
    summary="Manually retry a failed job",
    description="Operator endpoint to re-enqueue a permanently failed job into a new retry cycle.",
)
def retry_job(
    job_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
) -> JobRetryResponse:
    """Manually re-enqueue a permanently failed job."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job with ID '{job_id}' not found.",
        )

    if job.status != JobStatus.FAILED.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Cannot manually retry job in '{job.status}' status. "
                f"Only '{JobStatus.FAILED.value}' jobs can be retried."
            ),
        )

    # Reset retry cycle while preserving attempt history
    job.status = JobStatus.QUEUED.value
    job.attempts = 0
    job.locked_by = None
    job.lease_expires_at = None
    job.available_at = func.now()
    job.updated_at = func.now()

    db.commit()
    db.refresh(job)

    logger.info(
        "Job manually retried by operator",
        extra={"job_id": str(job.id), "event": "manual_retry_requested"},
    )

    return JobRetryResponse(
        job_id=job.id,
        status=job.status,
        message="Job re-enqueued for execution.",
    )
