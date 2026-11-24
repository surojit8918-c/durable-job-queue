"""Job and Attempt status enumerations."""

from enum import StrEnum


class JobStatus(StrEnum):
    """Enumeration of possible job lifecycle states."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRYING = "RETRYING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class AttemptStatus(StrEnum):
    """Enumeration of individual execution attempt states."""

    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ABANDONED = "ABANDONED"
