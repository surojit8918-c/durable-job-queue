"""Application configuration settings using Pydantic Settings."""

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Strongly typed application configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_ENV: str = "development"

    # PostgreSQL Database URLs (using psycopg v3 driver)
    DATABASE_URL: str = "postgresql+psycopg://postgres:12345678@localhost:5432/job_queue"
    TEST_DATABASE_URL: str = "postgresql+psycopg://postgres:12345678@localhost:5432/job_queue_test"

    # Worker configuration
    WORKER_ID: str = "worker-1"
    WORKER_POLL_INTERVAL: float = 1.0

    # Lease and timeouts
    LEASE_DURATION_SECONDS: int = 60
    LEASE_RENEWAL_INTERVAL_SECONDS: int = 15
    HANDLER_TIMEOUT_SECONDS: int = 30
    GRACEFUL_SHUTDOWN_SECONDS: int = 35

    # HTTP client timeouts
    HTTP_CONNECT_TIMEOUT_SECONDS: float = 5.0
    HTTP_READ_TIMEOUT_SECONDS: float = 20.0

    # Retry configuration
    MAX_JOB_ATTEMPTS: int = 5
    RETRY_BASE_DELAY_SECONDS: float = 2.0
    RETRY_MAX_DELAY_SECONDS: float = 60.0

    # External Partner API configuration
    PARTNER_API_BASE_URL: str = ""
    PARTNER_API_KEY: str = ""

    @model_validator(mode="after")
    def validate_timeouts_and_leases(self) -> "Settings":
        """Validate the relationship between lease duration, handler timeout, and renewal interval."""
        if self.LEASE_DURATION_SECONDS <= self.LEASE_RENEWAL_INTERVAL_SECONDS:
            raise ValueError(
                f"LEASE_DURATION_SECONDS ({self.LEASE_DURATION_SECONDS}) must be greater than "
                f"LEASE_RENEWAL_INTERVAL_SECONDS ({self.LEASE_RENEWAL_INTERVAL_SECONDS})"
            )
        if self.LEASE_RENEWAL_INTERVAL_SECONDS <= 0:
            raise ValueError("LEASE_RENEWAL_INTERVAL_SECONDS must be positive")
        if self.HANDLER_TIMEOUT_SECONDS <= 0:
            raise ValueError("HANDLER_TIMEOUT_SECONDS must be positive")
        if self.MAX_JOB_ATTEMPTS < 1:
            raise ValueError("MAX_JOB_ATTEMPTS must be at least 1")
        if self.RETRY_BASE_DELAY_SECONDS <= 0:
            raise ValueError("RETRY_BASE_DELAY_SECONDS must be positive")
        return self


settings = Settings()
