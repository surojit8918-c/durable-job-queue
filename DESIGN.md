# System Design & Architecture: Durable Job Queue

## 1. System Overview

The **Durable Job Queue** is an asynchronous background job processing system built with FastAPI, PostgreSQL 15+, and SQLAlchemy 2.0. PostgreSQL acts as the single source of truth and coordination layer, avoiding any external message broker (such as Redis, RabbitMQ, Kafka, or Celery).

The primary system invariants are:
1. **Durability**: Every acknowledged job is committed to PostgreSQL before returning HTTP 201/200.
2. **Safe Concurrency**: Multiple independent worker processes claim jobs atomically without double-processing.
3. **Crash Resilience**: If a worker abruptly crashes, the job remains persisted, its lease naturally expires, and it is recovered.
4. **Stale Worker Fencing**: Monotonically incrementing fencing tokens (`lease_token`) prevent zombie or delayed workers from overwriting newer progress.
5. **At-Least-Once Delivery**: Jobs are guaranteed to execute at least once; handlers are expected to be idempotent.
6. **Submission Idempotency**: Clients supply `Idempotency-Key` headers with request payloads to prevent duplicate job creation.

---

## 2. Architecture & Components

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

### 2.1 API Server
- Built on FastAPI.
- Handles `POST /jobs` (creation with idempotency checks), `GET /jobs/{id}` (status inquiry), `GET /stats` (operational indicators), and `POST /jobs/{id}/retry` (manual retry for failed jobs).
- Validates job types against registered handlers and enforces input schemas.
- Never runs background job payloads inside the HTTP request cycle.

### 2.2 Workers
- Independent Python CLI processes (`python -m app.workers.worker`).
- Single-threaded or multi-concurrency event loops polling PostgreSQL on a configurable interval (`WORKER_POLL_INTERVAL`).
- Atomically claims eligible jobs using `SELECT ... FOR UPDATE SKIP LOCKED` inside a short database transaction.
- Executes handlers strictly outside the database transaction.
- Periodically renews leases for long-running jobs via an asynchronous background renewal loop.
- Handles SIGTERM / SIGINT for graceful shutdown (finishing in-flight work within grace period).

### 2.3 Recovery Engine
- Scans for jobs stuck in `RUNNING` status whose `lease_expires_at <= now()`.
- Marks the corresponding attempt as `ABANDONED`.
- If `attempts < max_attempts`, re-enqueues the job into `RETRYING` or `QUEUED` state with backoff.
- If `attempts >= max_attempts`, transitions the job to terminal `FAILED`.

---

## 3. Database Schema Design

### 3.1 `jobs` Table
- `id` (UUID, Primary Key)
- `job_type` (VARCHAR(100), Indexed)
- `payload` (JSONB)
- `status` (VARCHAR(20), Enum: `QUEUED`, `RUNNING`, `RETRYING`, `SUCCEEDED`, `FAILED`)
- `priority` (INTEGER, Default: 0) - higher number indicates higher priority
- `run_at` (TIMESTAMPTZ, Default: `now()`) - initial scheduled execution timestamp
- `available_at` (TIMESTAMPTZ, Default: `now()`) - earliest eligible time for execution
- `attempts` (INTEGER, Default: 0) - count of started attempts in the current retry cycle
- `max_attempts` (INTEGER, Default: 5)
- `result` (JSONB, Nullable)
- `last_error` (TEXT, Nullable)
- `locked_by` (VARCHAR(100), Nullable) - identifier of the claiming worker
- `lease_expires_at` (TIMESTAMPTZ, Nullable) - timestamp when ownership expires
- `lease_token` (BIGINT, Default: 0) - monotonic fencing token
- `created_at` (TIMESTAMPTZ, Default: `now()`)
- `updated_at` (TIMESTAMPTZ, Default: `now()`)

### 3.2 `job_attempts` Table
- `id` (UUID, Primary Key)
- `job_id` (UUID, FK -> `jobs.id` ON DELETE CASCADE)
- `attempt_number` (INTEGER)
- `worker_id` (VARCHAR(100))
- `status` (VARCHAR(20), Enum: `RUNNING`, `SUCCEEDED`, `FAILED`, `ABANDONED`)
- `started_at` (TIMESTAMPTZ, Default: `now()`)
- `finished_at` (TIMESTAMPTZ, Nullable)
- `error_message` (TEXT, Nullable)
- `result` (JSONB, Nullable)
- `lease_token` (BIGINT)

### 3.3 `idempotency_keys` Table
- `id` (UUID, Primary Key)
- `idempotency_key` (VARCHAR(255), Unique)
- `request_hash` (VARCHAR(64)) - SHA256 of canonical JSON request
- `job_id` (UUID, FK -> `jobs.id` ON DELETE CASCADE)
- `created_at` (TIMESTAMPTZ, Default: `now()`)

### 3.4 Key Indexes
- `ix_jobs_claim_opt`: Partial B-Tree index on `(priority DESC, created_at ASC) WHERE status IN ('QUEUED', 'RETRYING')`.
  - **Rationale**: The atomic claiming query uses `ORDER BY priority DESC, created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED`. Without this index, PostgreSQL performs a full sequential scan followed by an in-memory quicksort over all pending jobs. The partial index allows PostgreSQL to satisfy the exact sort order directly via index traversal with zero sort memory allocations.
  - **Trade-offs & Behavior with Scheduled Jobs**: Since `available_at <= now()` is a dynamic inequality filter, it is evaluated during index scan filtering. If high-priority jobs are scheduled far in the future, the index scan inspects them and skips them via filter evaluation before claiming the earliest available row. Because the index only includes `QUEUED` and `RETRYING` jobs, its size is minimal and index maintenance overhead for historical `SUCCEEDED`/`FAILED` jobs is zero.
- `ix_jobs_claimable`: Composite index on `(status, available_at, priority, created_at)` retained for backward compatibility with PRD specifications.
- `ix_jobs_recovery`: Composite index on `(status, lease_expires_at)` for crash recovery sweep queries (`WHERE status = 'RUNNING' AND lease_expires_at <= now()`).
- `ix_jobs_job_type`: Single-column index for job-type queries and metrics.
- `ix_job_attempts_job_id`: Foreign key lookup index on `job_attempts(job_id)`.
- `ix_idempotency_keys_idempotency_key`: Unique index on `idempotency_keys(idempotency_key)`.

---

## 4. State Machine & Transitions

```text
       +---------------------------------------------+
       |                                             | (Manual Retry)
       v                                             |
   [QUEUED] <-------+                                |
       |            | (Retry scheduled)              |
       | Claim      |                                |
       v            |                                |
   [RUNNING] ---> [RETRYING]                         |
       |                                             |
       +------------> [SUCCEEDED]                    |
       |                                             |
       +------------> [FAILED] ----------------------+
```

### Transition Invariants:
1. `QUEUED` -> `RUNNING`: Claimed by a worker; `attempts += 1`, `lease_token += 1`, `locked_by = worker_id`, `lease_expires_at = now() + lease_duration`.
2. `RUNNING` -> `SUCCEEDED`: Handler completes successfully with valid `lease_token`.
3. `RUNNING` -> `RETRYING`: Transient error, `attempts < max_attempts`. Backoff computed, `available_at = now() + delay`, `locked_by = NULL`, `lease_expires_at = NULL`.
4. `RUNNING` -> `FAILED`: Non-retryable error OR `attempts >= max_attempts`.
5. `RUNNING` -> `RETRYING` or `FAILED` (via Recovery): Lease expired while in `RUNNING`. Attempt marked `ABANDONED`.
6. `FAILED` -> `QUEUED` (Manual Retry): `attempts` reset to 0 (new cycle), history preserved in `job_attempts`, `status = QUEUED`, `locked_by = NULL`.

---

## 5. Stale Worker Protection (Fencing Tokens)

To prevent split-brain and zombie workers (e.g. paused by GC or slow network while their lease expired and the job was reclaimed by Worker 2):
1. When Worker 1 claims a job, `lease_token` is incremented (e.g. from 1 to 2).
2. If Worker 1 takes longer than `lease_duration`, the recovery process re-enqueues the job.
3. Worker 2 claims the job; `lease_token` is incremented to 3.
4. When Worker 1 finally finishes and tries to finalize the job:
   ```sql
   UPDATE jobs
   SET status = 'SUCCEEDED', result = :result, locked_by = NULL, lease_expires_at = NULL, updated_at = now()
   WHERE id = :job_id AND lease_token = 2 AND locked_by = 'worker-1';
   ```
5. The `UPDATE` matches 0 rows because `lease_token` is now 3. Worker 1 detects the fencing violation, logs a warning, and discards its stale write.

---

## 6. Submission Idempotency Strategy

1. Client sends request with `Idempotency-Key: <key>`.
2. Server computes `request_hash = sha256(canonical_json(job_type, payload, priority, run_at))`.
3. Inside an atomic transaction:
   - Query `idempotency_keys` for `<key>`.
   - If found:
     - Compare stored `request_hash` with incoming `request_hash`.
     - If matches: fetch and return existing job details.
     - If differs: raise HTTP 409 Conflict.
   - If not found:
     - Insert job into `jobs`.
     - Insert record into `idempotency_keys`.
     - Commit transaction and return HTTP 201 Created.

---

## 7. Retries, Backoff, and Jitter

- Base delay: 2.0s
- Exponential backoff: `delay = min(max_delay, base_delay * (2 ** (attempt - 1)))`
- Full jitter: `actual_delay = random.uniform(0, delay)`
- `Retry-After` header: If handler returns or raises an HTTP 429 exception with `Retry-After`, that duration overrides the exponential backoff calculation.
