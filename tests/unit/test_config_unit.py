"""Unit tests for application configuration validation rules."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_settings_validation_lease_and_renewal_relationship() -> None:
    """Verify that LEASE_DURATION_SECONDS must exceed LEASE_RENEWAL_INTERVAL_SECONDS."""
    # When lease duration is less than or equal to renewal interval, validation must fail
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            LEASE_DURATION_SECONDS=10,
            LEASE_RENEWAL_INTERVAL_SECONDS=15,
        )
    assert "LEASE_DURATION_SECONDS (10) must be greater than" in str(exc_info.value)

    # Equal values must also fail
    with pytest.raises(ValidationError):
        Settings(
            LEASE_DURATION_SECONDS=15,
            LEASE_RENEWAL_INTERVAL_SECONDS=15,
        )


def test_settings_validation_positive_constraints() -> None:
    """Verify positive number constraints on retry and timeout settings."""
    # Negative or zero lease renewal interval
    with pytest.raises(ValidationError):
        Settings(LEASE_RENEWAL_INTERVAL_SECONDS=0)

    # Negative or zero handler timeout
    with pytest.raises(ValidationError):
        Settings(HANDLER_TIMEOUT_SECONDS=0)

    # Negative or zero max attempts
    with pytest.raises(ValidationError):
        Settings(MAX_JOB_ATTEMPTS=0)

    # Negative retry base delay
    with pytest.raises(ValidationError):
        Settings(RETRY_BASE_DELAY_SECONDS=-1.0)


def test_settings_valid_defaults() -> None:
    """Verify that default settings instantiate cleanly and satisfy all validation rules."""
    cfg = Settings(
        LEASE_DURATION_SECONDS=60,
        LEASE_RENEWAL_INTERVAL_SECONDS=15,
        HANDLER_TIMEOUT_SECONDS=30,
        MAX_JOB_ATTEMPTS=5,
        RETRY_BASE_DELAY_SECONDS=2.0,
    )
    assert cfg.LEASE_DURATION_SECONDS == 60
    assert cfg.LEASE_RENEWAL_INTERVAL_SECONDS == 15
    assert cfg.HANDLER_TIMEOUT_SECONDS == 30
    assert cfg.MAX_JOB_ATTEMPTS == 5
