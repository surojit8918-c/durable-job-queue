"""Partner API integration and mock adapter for background job processing."""

import contextlib
import json
import os
import random
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import logger
from app.handlers.registry import register_handler
from app.queue.retry import PermanentJobError, RetryableJobError


class StateList(list[dict[str, Any]]):
    """List that syncs clear operations back to the adapter persistent state."""

    def __init__(
        self,
        iterable: Any = (),
        on_change: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(iterable)
        self._on_change = on_change

    def clear(self) -> None:
        super().clear()
        if self._on_change:
            self._on_change()


class MockPartnerAdapter:
    """Simulates realistic external partner API behavior with configurable fault injection, thread safety, and cross-process state."""

    def __init__(
        self,
        failure_rate: float = 0.0,
        rate_limit_rate: float = 0.0,
        timeout_rate: float = 0.0,
        latency_seconds: float = 0.0,
        state_file: str | Path | None = None,
    ) -> None:
        self.failure_rate = failure_rate
        self.rate_limit_rate = rate_limit_rate
        self.timeout_rate = timeout_rate
        self.latency_seconds = latency_seconds

        env_state_file = os.environ.get("MOCK_PARTNER_STATE_FILE")
        self.state_file = Path(state_file or env_state_file or ".mock_partner_state.json")
        self._lock = threading.RLock()
        self._http_attempts: list[dict[str, Any]] = []
        self._successful_responses: list[dict[str, Any]] = []
        self._executed_business_operations: list[dict[str, Any]] = []
        self._processed_keys: set[str] = set()
        self._load_state()

    def _load_state(self) -> None:
        """Load state from persistent file if it exists."""
        if self.state_file.exists():
            with contextlib.suppress(Exception):
                text_content = self.state_file.read_text(encoding="utf-8")
                if not text_content.strip():
                    return
                data = json.loads(text_content)
                self._http_attempts = data.get("http_attempts", data.get("received_operations", []))
                self._successful_responses = data.get("successful_responses", [])
                self._executed_business_operations = data.get("executed_business_operations", [])
                self._processed_keys = set(data.get("processed_keys", []))

    def _save_state(self) -> None:
        """Save state to persistent file for cross-process visibility."""
        data = {
            "http_attempts": self._http_attempts,
            "received_operations": self._http_attempts,
            "successful_responses": self._successful_responses,
            "executed_business_operations": self._executed_business_operations,
            "processed_keys": list(self._processed_keys),
        }
        with contextlib.suppress(Exception):
            temp_file = self.state_file.with_suffix(".tmp")
            temp_file.write_text(json.dumps(data), encoding="utf-8")
            temp_file.replace(self.state_file)

    def _clear_http_attempts(self) -> None:
        with self._lock:
            self._http_attempts.clear()
            self._save_state()

    def _clear_successful_responses(self) -> None:
        with self._lock:
            self._successful_responses.clear()
            self._save_state()

    def _clear_executed_business_operations(self) -> None:
        with self._lock:
            self._executed_business_operations.clear()
            self._save_state()

    @property
    def http_attempts(self) -> list[dict[str, Any]]:
        with self._lock:
            self._load_state()
            return StateList(self._http_attempts, on_change=self._clear_http_attempts)

    @property
    def received_operations(self) -> list[dict[str, Any]]:
        """Backward compatibility alias for http_attempts."""
        return self.http_attempts

    @property
    def successful_responses(self) -> list[dict[str, Any]]:
        with self._lock:
            self._load_state()
            return StateList(self._successful_responses, on_change=self._clear_successful_responses)

    @property
    def executed_business_operations(self) -> list[dict[str, Any]]:
        with self._lock:
            self._load_state()
            return StateList(
                self._executed_business_operations,
                on_change=self._clear_executed_business_operations,
            )

    @property
    def processed_keys(self) -> set[str]:
        with self._lock:
            self._load_state()
            return set(self._processed_keys)

    def clear(self) -> None:
        """Reset all mock partner state and remove persistent state file."""
        with self._lock:
            self._http_attempts.clear()
            self._successful_responses.clear()
            self._executed_business_operations.clear()
            self._processed_keys.clear()
            self.failure_rate = 0.0
            self.rate_limit_rate = 0.0
            self.timeout_rate = 0.0
            self.latency_seconds = 0.0
            if self.state_file.exists():
                with contextlib.suppress(Exception):
                    self.state_file.unlink()
            temp_file = self.state_file.with_suffix(".tmp")
            if temp_file.exists():
                with contextlib.suppress(Exception):
                    temp_file.unlink()

    def call(self, endpoint: str, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        """Execute mock partner call with simulated network failure modes and idempotency deduplication."""
        with self._lock:
            self._load_state()
            self._http_attempts.append(
                {
                    "endpoint": endpoint,
                    "payload": payload,
                    "idempotency_key": idempotency_key,
                    "timestamp": time.time(),
                }
            )
            self._save_state()

        if self.latency_seconds > 0:
            time.sleep(self.latency_seconds)

        if self.timeout_rate > 0 and random.random() < self.timeout_rate:
            logger.warning("Mock partner adapter simulated timeout")
            raise TimeoutError("Mock partner connection timed out")

        if self.rate_limit_rate > 0 and random.random() < self.rate_limit_rate:
            logger.warning("Mock partner adapter simulated HTTP 429 rate limit")
            raise RetryableJobError("Partner API rate limit exceeded (429)", retry_after=2.0)

        if self.failure_rate > 0 and random.random() < self.failure_rate:
            logger.warning("Mock partner adapter simulated transient 503 error")
            raise RetryableJobError("Partner API transient unavailable (503)")

        # Validate mandatory business fields
        if (
            "customer_id" not in payload
            and "order_id" not in payload
            and "fail_permanently" not in payload
        ):
            pass

        if payload.get("fail_permanently"):
            logger.error("Mock partner adapter simulated permanent client error (400)")
            raise PermanentJobError("Invalid business operation payload")

        if payload.get("fail_retryable"):
            logger.warning("Mock partner adapter simulated retryable error")
            raise RetryableJobError("Simulated transient error")

        with self._lock:
            self._load_state()
            # Check if operation was already executed under this idempotency key
            if idempotency_key in self._processed_keys:
                logger.info(
                    f"Mock partner recognized duplicate key '{idempotency_key}'; returned existing result without side effects"
                )
                self._successful_responses.append(
                    {
                        "endpoint": endpoint,
                        "payload": payload,
                        "idempotency_key": idempotency_key,
                        "deduplicated": True,
                        "timestamp": time.time(),
                    }
                )
                self._save_state()
                return {
                    "partner_status": "success",
                    "operation_id": f"op-{idempotency_key}",
                    "deduplicated": True,
                    "processed_payload": payload,
                }

            # First execution of this business operation
            self._executed_business_operations.append(
                {
                    "endpoint": endpoint,
                    "payload": payload,
                    "idempotency_key": idempotency_key,
                    "timestamp": time.time(),
                }
            )
            self._processed_keys.add(idempotency_key)
            self._successful_responses.append(
                {
                    "endpoint": endpoint,
                    "payload": payload,
                    "idempotency_key": idempotency_key,
                    "deduplicated": False,
                    "timestamp": time.time(),
                }
            )
            self._save_state()

            return {
                "partner_status": "success",
                "operation_id": f"op-{idempotency_key}",
                "deduplicated": False,
                "processed_payload": payload,
            }


# Global adapter instance for test injection
mock_partner = MockPartnerAdapter()


@register_handler("partner_api_call")
def handle_partner_api_call(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute partner API operation with timeout, failure classification, and idempotency."""
    operation = payload.get("operation", "sync_customer")
    customer_id = payload.get("customer_id")
    stable_key = f"partner-{payload.get('job_id', customer_id or 'anon')}"

    # If external URL configured, make real HTTP call
    if settings.PARTNER_API_BASE_URL and settings.PARTNER_API_BASE_URL.startswith("http"):
        url = f"{settings.PARTNER_API_BASE_URL.rstrip('/')}/{operation}"
        headers = {
            "X-Idempotency-Key": stable_key,
            "Content-Type": "application/json",
        }
        if settings.PARTNER_API_KEY:
            headers["Authorization"] = f"Bearer {settings.PARTNER_API_KEY}"

        timeout = httpx.Timeout(
            connect=settings.HTTP_CONNECT_TIMEOUT_SECONDS,
            read=settings.HTTP_READ_TIMEOUT_SECONDS,
            write=10.0,
            pool=5.0,
        )

        with httpx.Client(timeout=timeout) as client:
            try:
                response = client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                json_res: dict[str, Any] = response.json()
                return json_res
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    retry_after = exc.response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else 2.0
                    raise RetryableJobError(
                        "Partner rate limited (429)", retry_after=delay
                    ) from exc
                if exc.response.status_code in (500, 502, 503, 504):
                    raise RetryableJobError(
                        f"Partner transient server error ({exc.response.status_code})"
                    ) from exc
                raise PermanentJobError(
                    f"Partner rejected operation ({exc.response.status_code})"
                ) from exc
            except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError) as exc:
                raise RetryableJobError(f"Partner connection failure: {exc}") from exc

    # Default to mock partner adapter
    return mock_partner.call(endpoint=operation, payload=payload, idempotency_key=stable_key)
