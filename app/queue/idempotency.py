"""Idempotency hash utilities."""

import hashlib
import json
from datetime import datetime
from typing import Any


def compute_request_hash(
    job_type: str,
    payload: dict[str, Any],
    priority: int,
    run_at: datetime | None,
) -> str:
    """Compute deterministic SHA-256 hash for normalized job submission request."""
    # Canonical JSON representation with sorted keys and compact separators
    canonical_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    iso_run_at = run_at.isoformat() if run_at is not None else ""

    raw_signature = f"{job_type}|{canonical_payload}|{priority}|{iso_run_at}"
    return hashlib.sha256(raw_signature.encode("utf-8")).hexdigest()
