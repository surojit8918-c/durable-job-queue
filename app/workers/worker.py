"""Worker process implementation for polling, claiming, and executing background jobs."""

import concurrent.futures
import threading
import time
import uuid
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

# Import handlers to register their job types
import app.handlers.partner_api  # noqa: F401
from app.core.config import settings
from app.core.logging import logger
from app.db.session import engine
from app.handlers.registry import get_handler
from app.queue.claiming import claim_next_job
from app.queue.lease import renew_lease
from app.queue.recovery import recover_expired_leases
from app.queue.retry import PermanentJobError
from app.queue.state import complete_job_failure, complete_job_success
from app.workers.lifecycle import lifecycle

WorkerSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Worker:
    """Independent worker that polls PostgreSQL, claims jobs, and runs handlers."""

    def __init__(
        self,
        worker_id: str | None = None,
        poll_interval: float | None = None,
        lease_duration: int | None = None,
        lease_renewal_interval: int | None = None,
        handler_timeout: int | None = None,
        base_retry_delay: float | None = None,
        max_retry_delay: float | None = None,
        session_factory: sessionmaker[Session] | None = None,
    ) -> None:
        self.worker_id = worker_id or settings.WORKER_ID
        self.poll_interval = poll_interval or settings.WORKER_POLL_INTERVAL
        self.lease_duration = lease_duration or settings.LEASE_DURATION_SECONDS
        self.lease_renewal_interval = (
            lease_renewal_interval or settings.LEASE_RENEWAL_INTERVAL_SECONDS
        )
        self.handler_timeout = handler_timeout or settings.HANDLER_TIMEOUT_SECONDS
        self.base_retry_delay = base_retry_delay or settings.RETRY_BASE_DELAY_SECONDS
        self.max_retry_delay = max_retry_delay or settings.RETRY_MAX_DELAY_SECONDS
        self.session_factory = session_factory or WorkerSession
        self._renewal_stop_event = threading.Event()

    def _start_lease_renewer(
        self,
        job_id: uuid.UUID,
        lease_token: int,
        lost_lease_flag: list[bool],
    ) -> threading.Thread:
        """Start a background daemon thread that periodically renews the job lease."""
        self._renewal_stop_event.clear()

        def renew_loop() -> None:
            while not self._renewal_stop_event.wait(self.lease_renewal_interval):
                with self.session_factory() as db:
                    renewed = renew_lease(
                        db=db,
                        job_id=job_id,
                        worker_id=self.worker_id,
                        lease_token=lease_token,
                        lease_duration_seconds=self.lease_duration,
                    )
                    if not renewed:
                        lost_lease_flag[0] = True
                        logger.error(
                            "Lost ownership during execution; aborting completion",
                            extra={"job_id": str(job_id), "worker_id": self.worker_id},
                        )
                        break

        thread = threading.Thread(target=renew_loop, daemon=True)
        thread.start()
        return thread

    def process_one_job(self, db: Session) -> bool:
        """Attempt to claim and process a single eligible job.

        Returns True if a job was claimed and processed, False if queue was empty.
        """
        # 1. First recover any expired leases awaiting recovery
        recover_expired_leases(
            db=db,
            base_retry_delay_seconds=self.base_retry_delay,
            max_retry_delay_seconds=self.max_retry_delay,
        )

        # 2. Atomically claim next job
        claimed = claim_next_job(
            db=db,
            worker_id=self.worker_id,
            lease_duration_seconds=self.lease_duration,
        )
        if claimed is None:
            return False

        job, attempt = claimed
        job_id = job.id
        attempt_id = attempt.id
        lease_token = job.lease_token
        job_type = job.job_type
        payload = job.payload

        # 3. Locate registered handler
        handler = get_handler(job_type)
        if handler is None:
            complete_job_failure(
                db=db,
                job_id=job_id,
                attempt_id=attempt_id,
                worker_id=self.worker_id,
                lease_token=lease_token,
                error=PermanentJobError(f"Job type '{job_type}' has no registered handler"),
            )
            return True

        # 4. Execute handler with background lease renewal and timeout
        lost_lease = [False]
        renewer_thread = self._start_lease_renewer(job_id, lease_token, lost_lease)

        result: dict[str, Any] | None = None
        execution_error: Exception | None = None

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(handler, payload)
            try:
                result = future.result(timeout=self.handler_timeout)
            except concurrent.futures.TimeoutError:
                execution_error = TimeoutError(
                    f"Handler timed out after {self.handler_timeout} seconds"
                )
            except Exception as exc:
                execution_error = exc
            finally:
                self._renewal_stop_event.set()
                renewer_thread.join(timeout=1.0)

        # 5. Check if lease was lost during execution
        if lost_lease[0]:
            logger.warning(
                "Lease lost while handler was running; discarding outcome",
                extra={"job_id": str(job_id), "worker_id": self.worker_id},
            )
            return True

        # 6. Finalize state in PostgreSQL
        if execution_error is not None:
            complete_job_failure(
                db=db,
                job_id=job_id,
                attempt_id=attempt_id,
                worker_id=self.worker_id,
                lease_token=lease_token,
                error=execution_error,
                base_retry_delay_seconds=self.base_retry_delay,
                max_retry_delay_seconds=self.max_retry_delay,
            )
        else:
            complete_job_success(
                db=db,
                job_id=job_id,
                attempt_id=attempt_id,
                worker_id=self.worker_id,
                lease_token=lease_token,
                result=result or {},
            )

        return True

    def run(self) -> None:
        """Continuous polling loop."""
        lifecycle.install_signal_handlers()
        logger.info(
            f"Worker '{self.worker_id}' started polling queue",
            extra={"worker_id": self.worker_id, "event": "worker_started"},
        )

        while not lifecycle.should_stop:
            try:
                with self.session_factory() as db:
                    job_processed = self.process_one_job(db)

                if not job_processed and not lifecycle.should_stop:
                    time.sleep(self.poll_interval)
            except Exception as e:
                logger.exception(
                    f"Unexpected error in worker loop: {e}",
                    extra={"worker_id": self.worker_id},
                )
                if not lifecycle.should_stop:
                    time.sleep(self.poll_interval)

        logger.info(
            f"Worker '{self.worker_id}' stopped gracefully",
            extra={"worker_id": self.worker_id, "event": "worker_stopped"},
        )


if __name__ == "__main__":
    worker = Worker()
    worker.run()
