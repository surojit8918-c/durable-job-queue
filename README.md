# Durable Job Queue System

A reliable, database-backed background job queue system using **PostgreSQL 15+** as the only infrastructure dependency, built with **Python 3.11**, **FastAPI**, **SQLAlchemy 2.0**, and **Alembic**.

Accepted background jobs remain durable, safe from concurrent worker collisions, and fully recoverable even when worker processes crash abruptly.

---

## Architecture Overview

```text
                  External Client
                        |
                        v
                 +--------------+
                 |   FastAPI    |
                 |  API Server  |
                 +--------------+
                        |
                        v
                 +--------------+
                 |  PostgreSQL  |
                 |              |
                 |    jobs      |
                 | job_attempts |
                 | idempotency  |
                 +--------------+
                        |
           Periodic Polling & Recovery
                        |
              +---------+---------+
              |                   |
              v                   v
        +-----------+       +-----------+
        | Worker 1  |       | Worker 2  |
        +-----------+       +-----------+
              |                   |
              +---------+---------+
                        |
                        v
                  Job Handlers
                        |
                        v
               External Partner API
```

### Core Architecture Principles
1. **Zero External Broker Dependency**: PostgreSQL handles all persistence, locking, and synchronization without Redis, RabbitMQ, Kafka, or Celery.
2. **Safe Atomic Job Claiming**: Workers atomically select and lock eligible jobs using `SELECT ... FOR UPDATE SKIP LOCKED` inside a short database transaction. Database locks are released before handler execution begins.
3. **Fencing Tokens (Stale Worker Protection)**: Monotonically increasing `lease_token` values ensure zombie workers (e.g. stalled by network or GC) cannot overwrite newer job results.
4. **Crash Recovery**: When a worker dies unexpectedly, its lease expires. The recovery engine detects expired leases, transitions the attempt to `ABANDONED`, and re-enqueues the job with backoff or transitions it to `FAILED` if maximum attempts are exhausted.
5. **Submission Idempotency**: Full `Idempotency-Key` header support with canonical SHA-256 request hashing. Duplicate submissions return the existing job; conflicting payloads return HTTP 409 Conflict.
6. **Graceful Shutdown**: Workers listen for `SIGINT` and `SIGTERM`, stopping new claims immediately while granting in-flight jobs a grace period to complete.

---

## Prerequisites

- **Python**: 3.11+
- **PostgreSQL**: 15+
- **Docker & Docker Compose** (for containerized deployment)

---

## Installation & Setup

### 1. Clone & Environment Setup
```bash
git clone <repository_url>
cd DurableJobQueue
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows:
.venv\Scripts\Activate.ps1
```

### 2. Install Dependencies
```bash
pip install -e ".[dev]"
```

### 3. Configure Environment Variables
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Default configuration values:
```env
APP_ENV=development
DATABASE_URL=postgresql+psycopg://postgres:12345678@localhost:5432/job_queue
TEST_DATABASE_URL=postgresql+psycopg://postgres:12345678@localhost:5432/job_queue_test

WORKER_ID=worker-1
WORKER_POLL_INTERVAL=1.0

LEASE_DURATION_SECONDS=60
LEASE_RENEWAL_INTERVAL_SECONDS=15
HANDLER_TIMEOUT_SECONDS=30
GRACEFUL_SHUTDOWN_SECONDS=35

MAX_JOB_ATTEMPTS=5
RETRY_BASE_DELAY_SECONDS=2.0
RETRY_MAX_DELAY_SECONDS=60.0
```

---

## Database Migrations

Run database migrations via Alembic:
```bash
# Apply migrations to head
alembic upgrade head

# Generate a new migration
alembic revision --autogenerate -m "migration_name"
```

---

## Running with Docker Compose

To start the complete infrastructure (PostgreSQL, FastAPI API server, automatic Alembic migrations, and 2 concurrent worker replicas):

```bash
docker compose up --build
```

Access the interactive API documentation at:
- **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)

---

## Running Locally

### Start API Server
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Start Independent Worker
```bash
python -m app.workers.worker
```
To run multiple workers concurrently, launch separate terminal sessions with distinct worker IDs:
```bash
WORKER_ID=worker-1 python -m app.workers.worker
WORKER_ID=worker-2 python -m app.workers.worker
```

---

## REST API Specification

### 1. Submit a Background Job
`POST /jobs`
- **Headers:** `Idempotency-Key` (Optional string)
- **Body:**
```json
{
  "job_type": "partner_api_call",
  "payload": {
    "customer_id": 101,
    "operation": "sync_customer"
  },
  "priority": 5,
  "run_at": "2026-11-20T12:00:00Z"
}
```
- **Response (201 Created / 200 OK):**
```json
{
  "job_id": "44640ecc-4880-47fd-bc06-3e2fb7d63c5e",
  "status": "QUEUED"
}
```

### 2. Retrieve Job Status and Result
`GET /jobs/{id}`
- **Response (200 OK):**
```json
{
  "job_id": "44640ecc-4880-47fd-bc06-3e2fb7d63c5e",
  "job_type": "partner_api_call",
  "status": "SUCCEEDED",
  "priority": 5,
  "attempts": 1,
  "max_attempts": 5,
  "result": {
    "partner_status": "success",
    "operation_id": "op-partner-101"
  },
  "last_error": null,
  "created_at": "2026-11-20T12:00:00Z",
  "updated_at": "2026-11-20T12:00:02Z"
}
```

### 3. Operational Monitoring Indicators
`GET /stats`
- **Response (200 OK):**
```json
{
  "total_jobs": 1500,
  "queued_jobs": 42,
  "running_jobs": 8,
  "retrying_jobs": 5,
  "succeeded_jobs": 1440,
  "failed_jobs": 5,
  "expired_leases": 0,
  "oldest_queued_job_age_seconds": 1.25
}
```

### 4. Manually Retry a Failed Job (Operator Endpoint)
`POST /jobs/{id}/retry`
- Re-enqueues a `FAILED` job into a new retry cycle while preserving all historical attempt logs in `job_attempts`.
- **Response (200 OK):**
```json
{
  "job_id": "44640ecc-4880-47fd-bc06-3e2fb7d63c5e",
  "status": "QUEUED",
  "message": "Job re-enqueued for execution."
}
```

---

## Testing & Verification

The test suite validates durability, concurrency, crash recovery, and soak performance.

### 1. Run Complete Test Suite
```bash
pytest
```

### 2. Run Concurrency Tests
Validates that multiple simultaneous workers never claim the same job:
```bash
pytest tests/concurrency
```

### 3. Run Chaos Tests
Simulates worker process `SIGKILL` termination and poison job multi-cycle recovery:
```bash
pytest tests/chaos
```

### 4. Run High-Volume Soak Benchmark
Execute the reproducible soak benchmark script:
```bash
python scripts/run_soak_test.py --jobs 10000 --workers 4 --failure-rate 0.05
```
A full written test report is generated at `SOAK_TEST_REPORT.md`.

> **Note on Benchmark Execution:** In our verification environment, the 10,000-job benchmark was interrupted at job 8,975 by an external host restart; all remaining 1,025 jobs were resumed via `python scripts/run_soak_recovery.py` and reached 100% terminal completion (`SUCCEEDED: 10,000`, `FAILED: 0`). Full disclosure, metrics, and attempt reconciliation are detailed in [SOAK_TEST_REPORT.md](file:///d:/Desktop/DurableJobQueue/SOAK_TEST_REPORT.md) and [SOAK_TEST_RECOVERY_REPORT.md](file:///d:/Desktop/DurableJobQueue/SOAK_TEST_RECOVERY_REPORT.md).

---

## Code Quality & Type Checking

Strict code quality gates without any inline type-checking suppressions:

```bash
# Code formatting and linting
ruff check app tests scripts
ruff format --check app tests scripts

# Static type verification (strict mode)
mypy app tests scripts
```

---

## Design and Limitations Documentation

- For in-depth architectural decisions, state machine diagrams, and fencing token semantics, see [DESIGN.md](file:///d:/Desktop/DurableJobQueue/DESIGN.md).
- For delivery guarantees, priority starvation notes, and trade-offs, see [LIMITATIONS.md](file:///d:/Desktop/DurableJobQueue/LIMITATIONS.md).
- For reproducible soak testing results, see [SOAK_TEST_REPORT.md](file:///d:/Desktop/DurableJobQueue/SOAK_TEST_REPORT.md).
