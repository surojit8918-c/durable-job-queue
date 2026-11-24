"""Job handler registry and dispatcher."""

from collections.abc import Callable
from typing import Any

# Handler signature: takes payload dict, returns result dict
JobHandler = Callable[[dict[str, Any]], dict[str, Any]]

_REGISTRY: dict[str, JobHandler] = {}


def register_handler(job_type: str) -> Callable[[JobHandler], JobHandler]:
    """Decorator to register a callable handler for a specific job_type."""

    def decorator(fn: JobHandler) -> JobHandler:
        _REGISTRY[job_type] = fn
        return fn

    return decorator


def get_handler(job_type: str) -> JobHandler | None:
    """Retrieve registered handler for a job type, or None if not registered."""
    return _REGISTRY.get(job_type)


def list_registered_job_types() -> list[str]:
    """Return all currently registered job type names."""
    return list(_REGISTRY.keys())


def is_valid_job_type(job_type: str) -> bool:
    """Check if a job type is registered in the system."""
    return job_type in _REGISTRY


# Basic builtin handlers for testing and verification
@register_handler("test_job")
def handle_test_job(payload: dict[str, Any]) -> dict[str, Any]:
    """Simple test handler echoing input with acknowledgement."""
    return {"message": "Test job completed", "echo": payload}


@register_handler("noop")
def handle_noop(payload: dict[str, Any]) -> dict[str, Any]:
    """No-op handler for benchmarking throughput."""
    return {"status": "ok"}
