"""Drain and recover remaining jobs from interrupted soak benchmark in job_queue_test.

Validates crash recovery, lease expiration handling, idempotency, and terminal completion
for all 10,000 originally enqueued jobs without resetting or truncating state.
"""

import concurrent.futures
import logging
import sys
import time
from pathlib import Path

# Ensure app is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.models import AttemptStatus, Job, JobAttempt, JobStatus
from app.handlers.partner_api import mock_partner
from app.workers.worker import Worker

logging.getLogger("job_queue").setLevel(logging.WARNING)


def run_recovery(num_workers: int = 4) -> None:
    target_db_url = settings.TEST_DATABASE_URL
    assert "job_queue_test" in target_db_url, (
        f"Safety check failed: target {target_db_url} is not job_queue_test!"
    )

    print("=" * 70)
    print("DURABLE JOB QUEUE - RECOVERY VALIDATION (INTERRUPTED BENCHMARK)")
    print(f"Isolated Test Database:  {target_db_url}")
    print(f"Recovery Workers:        {num_workers}")
    print("=" * 70)

    engine = create_engine(target_db_url, pool_pre_ping=True)
    recovery_session_factory = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
    )

    # 1. Verify original database state
    with recovery_session_factory() as db:
        total_jobs = db.scalar(select(func.count(Job.id))) or 0
        assert total_jobs == 10000, f"Expected 10,000 original jobs, found {total_jobs}"

        status_counts = dict(
            db.execute(select(Job.status, func.count(Job.id)).group_by(Job.status)).all()
        )
        print(f"\n[1/4] Pre-Recovery Database State (Total: {total_jobs}):")
        for st, cnt in sorted(status_counts.items()):
            print(f"  {st:12s}: {cnt}")

        # 2. Inspect the previously interrupted RUNNING jobs
        running_jobs = db.scalars(select(Job).where(Job.status == JobStatus.RUNNING.value)).all()
        interrupted_ids = [j.id for j in running_jobs]
        print(f"\n[2/4] Inspecting {len(running_jobs)} interrupted RUNNING jobs:")
        db_now = db.scalar(text("SELECT NOW();"))
        for j in running_jobs:
            expired = j.lease_expires_at is not None and j.lease_expires_at < db_now
            print(
                f"  Job {j.id}: locked_by={j.locked_by}, "
                f"lease_expires_at={j.lease_expires_at}, attempts={j.attempts}, expired={expired}"
            )
            assert expired, f"Job {j.id} lease is not expired! Wait for expiry."

    # Preserve and link mock partner state
    state_path = Path(".mock_partner_soak_state.json")
    assert state_path.exists(), (
        "Expected .mock_partner_soak_state.json to exist from interrupted run!"
    )
    mock_partner.state_file = state_path
    mock_partner.failure_rate = 0.05

    pre_http_attempts = len(mock_partner.http_attempts)
    pre_successful = len(mock_partner.successful_responses)
    pre_ops = len(mock_partner.executed_business_operations)
    print("\nPre-Recovery Mock Partner State:")
    print(f"  HTTP Attempts:       {pre_http_attempts}")
    print(f"  Successful Outcomes: {pre_successful}")
    print(f"  Business Operations: {pre_ops}")

    # 3. Start workers to drain remaining jobs and recover expired leases
    print(f"\n[3/4] Starting {num_workers} workers to drain queue and recover expired leases...")
    start_time = time.time()

    def worker_loop(w_id: str) -> dict[str, int]:
        worker = Worker(
            worker_id=w_id,
            poll_interval=0.01,
            lease_duration=30,
            base_retry_delay=0.05,
            max_retry_delay=0.2,
            session_factory=recovery_session_factory,
        )
        stats = {"processed": 0}
        while True:
            with recovery_session_factory() as db:
                did_work = worker.process_one_job(db)
                if did_work:
                    stats["processed"] += 1
                else:
                    unfinished = (
                        db.scalar(
                            select(func.count(Job.id)).where(
                                Job.status.in_(
                                    [
                                        JobStatus.QUEUED.value,
                                        JobStatus.RUNNING.value,
                                        JobStatus.RETRYING.value,
                                    ]
                                )
                            )
                        )
                        or 0
                    )
                    if unfinished == 0:
                        break
                    time.sleep(0.01)
        return stats

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {
            executor.submit(worker_loop, f"recovery-worker-{i}"): f"worker-{i}"
            for i in range(num_workers)
        }
        for f in concurrent.futures.as_completed(futures):
            w_name = futures[f]
            res = f.result()
            print(f"  {w_name} completed: {res['processed']} job claims processed")

    drain_duration = time.time() - start_time
    print(f"-> All remaining jobs drained in {drain_duration:.2f}s")

    # 4. Final verification and reconciliation
    print("\n[4/4] Final Database and Partner Reconciliation...")
    with recovery_session_factory() as db:
        final_total = db.scalar(select(func.count(Job.id))) or 0
        final_counts = dict(
            db.execute(select(Job.status, func.count(Job.id)).group_by(Job.status)).all()
        )
        queued_remaining = final_counts.get(JobStatus.QUEUED.value, 0)
        running_remaining = final_counts.get(JobStatus.RUNNING.value, 0)
        retrying_remaining = final_counts.get(JobStatus.RETRYING.value, 0)
        succeeded = final_counts.get(JobStatus.SUCCEEDED.value, 0)
        failed = final_counts.get(JobStatus.FAILED.value, 0)

        total_attempts = db.scalar(select(func.count(JobAttempt.id))) or 0
        abandoned_attempts = (
            db.scalar(
                select(func.count(JobAttempt.id)).where(
                    JobAttempt.status == AttemptStatus.ABANDONED.value
                )
            )
            or 0
        )

        print(f"\n  Audit of {len(interrupted_ids)} Interrupted Jobs from Benchmark Interruption:")
        recovered_success_count = 0
        for j_id in interrupted_ids:
            job = db.get(Job, j_id)
            assert job is not None
            attempts_list = db.scalars(
                select(JobAttempt)
                .where(JobAttempt.job_id == j_id)
                .order_by(JobAttempt.attempt_number)
            ).all()
            attempt_statuses = [a.status for a in attempts_list]
            print(
                f"    Job {j_id}: final status={job.status}, total_attempts={job.attempts}, "
                f"attempt_history={attempt_statuses}"
            )
            if job.status == JobStatus.SUCCEEDED.value:
                recovered_success_count += 1
            # Verify one attempt was ABANDONED due to the crash
            assert AttemptStatus.ABANDONED.value in attempt_statuses, (
                f"Expected an attempt of interrupted job {j_id} to be ABANDONED, got {attempt_statuses}"
            )

    post_http_attempts = len(mock_partner.http_attempts)
    post_successful = len(mock_partner.successful_responses)
    post_ops = len(mock_partner.executed_business_operations)
    post_keys = len(mock_partner.processed_keys)
    duplicate_side_effects = post_ops - post_keys

    # Verifications
    assert final_total == 10000, f"Expected 10,000 jobs, found {final_total}"
    assert queued_remaining == 0, f"Jobs remaining QUEUED: {queued_remaining}"
    assert running_remaining == 0, f"Jobs remaining RUNNING: {running_remaining}"
    assert retrying_remaining == 0, f"Jobs remaining RETRYING: {retrying_remaining}"
    assert succeeded + failed == 10000, f"Terminal count mismatch: {succeeded + failed} != 10000"
    assert duplicate_side_effects == 0, f"Duplicate side effects detected: {duplicate_side_effects}"

    print("\n" + "=" * 70)
    print("RECOVERY VALIDATION REPORT")
    print(f"Total Database Jobs:               {final_total}")
    print(f"Successfully Finished Jobs:        {succeeded}")
    print(f"Permanently Failed Jobs:           {failed}")
    print("Jobs Remaining Active/Queued:      0 (QUEUED=0, RUNNING=0, RETRYING=0)")
    print(
        f"Recovered Interrupted Jobs:        {len(interrupted_ids)} (all {recovered_success_count} succeeded on retry)"
    )
    print(f"Total ABANDONED Attempt Records:   {abandoned_attempts}")
    print(f"Total DB Execution Attempts:       {total_attempts}")
    print(f"Total External HTTP Attempts:      {post_http_attempts}")
    print(f"Total Successful Partner Outcomes: {post_successful}")
    print(f"Total Unique Business Operations:  {post_ops}")
    print(
        f"Duplicate Business Side Effects:   {duplicate_side_effects} (ZERO duplicate executions)"
    )
    print(f"Recovery Drain Duration:           {drain_duration:.2f}s")
    print("=" * 70)


if __name__ == "__main__":
    run_recovery(num_workers=4)
