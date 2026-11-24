"""Unit tests for retry delay calculation and error classification."""

import httpx

from app.queue.retry import (
    PermanentJobError,
    RetryableJobError,
    calculate_retry_delay,
    classify_error,
)


def test_calculate_retry_delay_exponential_and_jitter() -> None:
    """Verify exponential backoff calculation, ceiling caps, and random jitter range."""
    base = 2.0
    max_delay = 60.0

    # For attempt 1: delay capped at 2.0s, jitter between 0 and 2.0
    for _ in range(20):
        delay = calculate_retry_delay(
            attempt=1, base_delay_seconds=base, max_delay_seconds=max_delay
        )
        assert 0.0 <= delay <= 2.0

    # For attempt 2: delay capped at 4.0s
    for _ in range(20):
        delay = calculate_retry_delay(
            attempt=2, base_delay_seconds=base, max_delay_seconds=max_delay
        )
        assert 0.0 <= delay <= 4.0

    # For attempt 3: delay capped at 8.0s
    for _ in range(20):
        delay = calculate_retry_delay(
            attempt=3, base_delay_seconds=base, max_delay_seconds=max_delay
        )
        assert 0.0 <= delay <= 8.0

    # For high attempt number: delay capped at max_delay_seconds (60.0)
    for _ in range(20):
        delay = calculate_retry_delay(
            attempt=10, base_delay_seconds=base, max_delay_seconds=max_delay
        )
        assert 0.0 <= delay <= 60.0


def test_calculate_retry_delay_retry_after_override() -> None:
    """Verify that Retry-After parameter overrides exponential backoff up to max_delay."""
    # When retry_after is provided, it is directly returned
    delay = calculate_retry_delay(attempt=1, retry_after=12.5, max_delay_seconds=60.0)
    assert delay == 12.5

    # If retry_after exceeds max_delay, it is capped at max_delay
    delay_capped = calculate_retry_delay(attempt=1, retry_after=120.0, max_delay_seconds=60.0)
    assert delay_capped == 60.0


def test_classify_error_transient_and_permanent() -> None:
    """Verify classification of exceptions into retryable vs permanent."""
    # Custom exceptions
    assert classify_error(RetryableJobError("transient", retry_after=5.0)) == (True, 5.0)
    assert classify_error(PermanentJobError("fatal")) == (False, None)

    # Built-in network and timeout exceptions
    assert classify_error(TimeoutError("connection timed out")) == (True, None)
    assert classify_error(ConnectionError("host unreachable")) == (True, None)

    # HTTP client errors
    req = httpx.Request("GET", "https://example.com")

    # 5xx Server errors -> Retryable
    for status_code in (500, 502, 503, 504):
        err = httpx.HTTPStatusError(
            "server error", request=req, response=httpx.Response(status_code, request=req)
        )
        assert classify_error(err) == (True, None)

    # 429 Rate limited with Retry-After header
    resp_429 = httpx.Response(429, request=req, headers={"Retry-After": "30"})
    err_429 = httpx.HTTPStatusError("rate limited", request=req, response=resp_429)
    assert classify_error(err_429) == (True, 30.0)

    # 4xx Client errors -> Permanent
    for status_code in (400, 401, 403, 404, 422):
        err = httpx.HTTPStatusError(
            "client error", request=req, response=httpx.Response(status_code, request=req)
        )
        assert classify_error(err) == (False, None)

    # Programming/payload errors -> Permanent
    assert classify_error(ValueError("bad value")) == (False, None)
    assert classify_error(KeyError("missing key")) == (False, None)
    assert classify_error(TypeError("bad type")) == (False, None)
