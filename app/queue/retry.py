"""Retry calculations, error classifications, and backoff utilities."""

import random

import httpx


class JobError(Exception):
    """Base exception for job processing errors."""

    pass


class RetryableJobError(JobError):
    """Exception indicating an error that should be retried automatically."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class PermanentJobError(JobError):
    """Exception indicating an unrecoverable failure that should not be retried."""

    pass


def classify_error(exc: Exception) -> tuple[bool, float | None]:
    """Classify an exception as retryable or permanent.

    Returns:
        (is_retryable, retry_after_seconds)
    """
    if isinstance(exc, RetryableJobError):
        return True, exc.retry_after

    if isinstance(exc, PermanentJobError):
        return False, None

    # Handle httpx HTTP errors
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            # Respect Retry-After header if present
            retry_after_hdr = exc.response.headers.get("Retry-After")
            retry_after = None
            if retry_after_hdr:
                try:
                    retry_after = float(retry_after_hdr)
                except ValueError:
                    retry_after = None
            return True, retry_after
        if status in (500, 502, 503, 504):
            return True, None
        return False, None

    if isinstance(
        exc,
        (
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.ConnectError,
            TimeoutError,
            ConnectionError,
        ),
    ):
        return True, None

    # General payload, value, or programming errors are permanent
    if isinstance(exc, (KeyError, ValueError, TypeError)):
        return False, None

    # Default unrecognized exceptions to permanent to avoid infinite retry loops
    return False, None


def calculate_retry_delay(
    attempt: int,
    base_delay_seconds: float = 2.0,
    max_delay_seconds: float = 60.0,
    retry_after: float | None = None,
) -> float:
    """Calculate exponential backoff delay with full random jitter."""
    if retry_after is not None and retry_after > 0:
        return min(max_delay_seconds, retry_after)

    # Exponential backoff: base * 2^(attempt - 1)
    attempt_index = max(1, attempt) - 1
    delay = base_delay_seconds * (2**attempt_index)
    capped_delay = min(max_delay_seconds, delay)

    # Full jitter: random uniform between 0 and capped_delay
    return random.uniform(0.0, capped_delay)
