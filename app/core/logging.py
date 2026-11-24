"""Structured logging configuration for Durable Job Queue."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any


class JSONFormatter(logging.Formatter):
    """Custom JSON formatter producing structured log events."""

    def format(self, record: logging.LogRecord) -> str:
        log_record: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Include contextual attributes if present
        for attr in [
            "job_id",
            "attempt_id",
            "worker_id",
            "lease_token",
            "job_type",
            "status",
            "attempt_number",
            "event",
        ]:
            if hasattr(record, attr):
                log_record[attr] = getattr(record, attr)

        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_record)


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure and return root logger with structured JSON formatting."""
    root_logger = logging.getLogger("job_queue")
    root_logger.setLevel(level.upper())

    # Avoid duplicate handlers if already configured
    if not root_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONFormatter())
        root_logger.addHandler(handler)

    return root_logger


logger = setup_logging()
