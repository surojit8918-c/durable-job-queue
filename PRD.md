# PRODUCT REQUIREMENTS DOCUMENT (PRD)

# Durable Job Queue System

**Project Name:** Durable Job Queue
**Document Type:** Product Requirements Document
**Version:** 1.0
**Status:** Ready for Development
**Target Development Tool:** Google Antigravity
**Backend Language:** Python 3.11
**Framework:** FastAPI
**Database:** PostgreSQL 15
**ORM:** SQLAlchemy 2.0
**Migration Tool:** Alembic
**Testing:** pytest
**Code Quality:** Ruff + mypy
**Deployment:** Docker Compose

---

# 1. Product Overview

## 1.1 What Are We Building?

Build a reliable, database-backed background job queue system using PostgreSQL as the only infrastructure dependency.

The system will allow clients to submit background jobs through REST APIs. Jobs will be persisted in PostgreSQL and processed asynchronously by independent worker processes.

The system must support multiple concurrent workers, job prioritization, scheduled execution, automatic retries, crash recovery, duplicate submission prevention, and operational monitoring.

The primary objective is to demonstrate that accepted jobs remain durable and recoverable even when workers crash unexpectedly.

## 1.2 Problem Statement

Applications frequently need to perform operations that are slow, expensive, or unreliable.

Examples include:

- Sending emails.
- Processing uploaded documents.
- Generating reports.
- Calling third-party APIs.
- Synchronizing customer information.
- Executing scheduled operations.

Performing these operations directly inside an HTTP request can cause request timeouts, poor response times, and reliability issues.

A background job queue solves this by separating job submission from execution.

The client submits a job and immediately receives a job ID. Independent workers process the job asynchronously.

The client can later retrieve the job's status and result.

## 1.3 Product Vision

Create a small but production-minded background job processing system that demonstrates:

- Durable job storage.
- Safe concurrent execution.
- Reliable worker coordination.
- Crash recovery.
- At-least-once execution.
- Idempotent submission.
- Controlled retry behavior.
- Operational visibility.

Correctness and reliability must take priority over premature optimization.

---

# 2. Goals and Non-Goals

## 2.1 Goals

The system must:

1. Accept jobs through a REST API.
2. Persist accepted jobs in PostgreSQL.
3. Process jobs asynchronously.
4. Support multiple concurrent workers.
5. Prevent simultaneous claiming of the same job.
6. Support priority-based execution.
7. Support scheduled execution.
8. Retry retryable failures automatically.
9. Recover jobs abandoned by crashed workers.
10. Prevent stale workers from overwriting newer results.
11. Prevent duplicate job creation using idempotency keys.
12. Retain permanently failed jobs for inspection.
13. Support manual retry of failed jobs.
14. Expose job status, results, and operational statistics.
15. Provide automated reliability tests.
16. Run using Docker Compose.

## 2.2 Non-Goals

The initial version will NOT implement:

- Redis.
- Kafka.
- RabbitMQ.
- Celery.
- RQ.
- Any external message broker.
- Kubernetes.
- Distributed workflow orchestration.
- Exactly-once execution guarantees.
- A complex frontend administration dashboard.
- Multi-database support.

PostgreSQL must remain the only infrastructure dependency.

---

# 3. Target Users and Use Cases

## 3.1 API Client

An application that submits background jobs.

Responsibilities:

- Submit jobs.
- Provide job payloads.
- Optionally specify priority.
- Optionally specify execution time.
- Retrieve job status.
- Retrieve job results.

## 3.2 Worker

An independent background process that executes jobs.

Responsibilities:

- Discover eligible jobs.
- Atomically claim jobs.
- Execute registered handlers.
- Renew job leases.
- Record execution results.
- Handle failures.
- Schedule retries.

## 3.3 Operator

A person responsible for monitoring the system.

Responsibilities:

- Inspect job states.
- Identify stuck jobs.
- Inspect failed attempts.
- View system statistics.
- Manually retry permanently failed jobs.

---

# 4. Technology Stack

Use the following mandatory technologies.

| Layer                   | Technology           |
| ----------------------- | -------------------- |
| Language                | Python 3.11          |
| API framework           | FastAPI              |
| Database                | PostgreSQL 15        |
| ORM                     | SQLAlchemy 2.0       |
| Migrations              | Alembic              |
| Data validation         | Pydantic             |
| HTTP client             | httpx                |
| Testing                 | pytest               |
| Code formatting/linting | Ruff                 |
| Type checking           | mypy                 |
| Containerization        | Docker               |
| Orchestration           | Docker Compose       |
| API documentation       | Swagger UI / OpenAPI |

Do not introduce unnecessary dependencies.

All dependency choices must be documented in the project.

---

# 5. System Architecture

## 5.1 High-Level Architecture

The system will consist of four major components:

1. API Server.
2. PostgreSQL Database.
3. Independent Worker Processes.
4. Job Handlers.

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
                 Periodic Polling
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

## 5.2 API Server Responsibilities

The API server must:

- Receive job submissions.
- Validate request data.
- Validate registered job types.
- Save jobs in PostgreSQL.
- Handle idempotency.
- Return job IDs.
- Return job status and results.
- Provide system statistics.

The API server must not execute long-running jobs directly.

## 5.3 Database Responsibilities

PostgreSQL must provide:

- Durable job storage.
- Job state management.
- Atomic job claiming.
- Worker coordination.
- Lease ownership information.
- Execution history.
- Idempotency records.
- Operational statistics.

## 5.4 Worker Responsibilities

Workers must:

1. Poll PostgreSQL.
2. Find eligible jobs.
3. Atomically claim a job.
4. Create an execution attempt.
5. Execute the appropriate handler.
6. Renew the lease when required.
7. Save the execution result.
8. Schedule retries after retryable failures.
9. Stop claiming jobs during shutdown.
10. Allow lease recovery after unexpected termination.

Workers must run independently from the API server.

Multiple worker processes must be supported.

## 5.5 Job Handler Architecture

Use a job-type registry.

Each job type must map to a registered handler.

Example:

```python
def process_partner_request(payload: dict) -> dict:
    # Execute partner API operation.
    # Validate response.
    # Return structured result.
    pass
```

The worker must select the appropriate handler using the job's `job_type`.

Never execute arbitrary Python code received through a job payload.

---

# 6. Functional Requirements

## FR-01: Job Submission

The system must allow clients to submit jobs.

Required information:

- Job type.
- Payload.

Optional information:

- Priority.
- Scheduled execution time.
- Idempotency key.

A successfully accepted job must be committed to PostgreSQL before the API returns success.

Default values must be applied when optional fields are missing.

## FR-02: Job Status Retrieval

Clients must be able to retrieve:

- Job ID.
- Job type.
- Current status.
- Priority.
- Attempt count.
- Maximum attempts.
- Creation time.
- Last update time.
- Result, if successful.
- Last error, if applicable.

## FR-03: Job Scheduling

Jobs must support future execution.

A job scheduled for a future time must not be claimed before its execution time.

Use PostgreSQL timestamps and database time for scheduling decisions.

## FR-04: Priority Scheduling

Jobs must support integer priority values.

Initial rule:

- Higher numeric value means higher priority.
- Jobs with equal priority are ordered by earlier creation time.

Example:

| Job   | Priority |
| ----- | -------: |
| Job A |       10 |
| Job B |        5 |
| Job C |        1 |

Expected order:

Job A → Job B → Job C.

Starvation of low-priority jobs must be documented as a possible limitation.

## FR-05: Concurrent Workers

Multiple workers must be able to process jobs simultaneously.

Use PostgreSQL transactions and:

`SELECT ... FOR UPDATE SKIP LOCKED`

Requirements:

- Workers must not claim the same available job simultaneously.
- Database locks must only be held during the claim transaction.
- Workers must execute handlers outside the claim transaction.
- Each successful claim must establish ownership information.

## FR-06: Automatic Retry

Retryable failures must be retried automatically.

Initial configuration:

- Maximum execution attempts: 5.
- Base delay: 2 seconds.
- Exponential backoff.
- Random jitter.
- Configurable maximum retry delay.
- Support for HTTP `Retry-After`.

A permanent failure should not be retried unnecessarily.

## FR-07: Crash Recovery

If a worker crashes while executing a job:

- The job must remain persisted.
- The job must not remain permanently stuck in RUNNING.
- The lease must eventually expire.
- The recovery mechanism must identify expired ownership.
- The previous attempt must be marked ABANDONED when appropriate.
- The job must become eligible for another attempt if retry capacity remains.
- Exhausted jobs must transition to FAILED.

## FR-08: Stale Worker Protection

Every ownership generation must have a fencing token.

When a worker claims a job:

- Assign worker ID.
- Set lease expiration.
- Increment lease token.

Every finalization operation must validate:

- Job status.
- Worker ownership.
- Current fencing token.

A worker with an outdated token must not update the job.

## FR-09: Submission Idempotency

The API must support the `Idempotency-Key` header.

Rules:

1. A new key creates a new job.
2. Same key + same request content returns the existing job.
3. Same key + different request content returns HTTP 409.
4. Concurrent duplicate submissions must not create duplicate jobs.
5. Job creation and idempotency record creation must be transactional.

Initial assumption: idempotency keys are globally unique.

If authenticated multi-client support is introduced, scope the key to the client identity.

## FR-10: Manual Retry

An operator must be able to manually retry a permanently failed job.

The manual retry operation must:

- Preserve previous attempt history.
- Reset the current retry-cycle counter according to the documented policy.
- Clear stale ownership information.
- Make the job eligible for execution.
- Perform state changes transactionally.

The additional endpoint is:

`POST /jobs/{id}/retry`

This is an additional endpoint, not a replacement for the three mandatory endpoints.

## FR-11: Operational Monitoring

The system must expose:

- Total jobs.
- Queued jobs.
- Running jobs.
- Retrying jobs.
- Succeeded jobs.
- Failed jobs.
- Expired leases.
- Oldest eligible queued job age.

The three most important operational indicators should be:

1. Queued job count.
2. Age of the oldest eligible queued job.
3. Number of expired leases awaiting recovery.

Active worker count must use an explicit heartbeat or registration mechanism if implemented.

---

# 7. Job State Machine

Implement the following states:

| State     | Meaning                                     |
| --------- | ------------------------------------------- |
| QUEUED    | Waiting for execution                       |
| RUNNING   | Currently claimed by a worker               |
| RETRYING  | Waiting for another attempt                 |
| SUCCEEDED | Successfully completed                      |
| FAILED    | Permanently failed or retry limit exhausted |

Expected lifecycle:

```text
QUEUED
   |
   v
RUNNING
   |
   +------> SUCCEEDED
   |
   +------> RETRYING
                 |
                 v
              RUNNING
                 |
                 +------> SUCCEEDED
                 |
                 +------> FAILED
```

Additional rules:

- Expired RUNNING jobs must enter recovery.
- A recovered job may become eligible for another execution.
- A manual retry may move an eligible FAILED job into the next retry cycle.
- Every transition must be validated.
- Invalid transitions must be rejected.

Do not allow arbitrary status updates through API requests.

---

# 8. Database Requirements

Use SQLAlchemy 2.0 models and Alembic migrations.

Create the following tables.

## 8.1 jobs

| Column           | Type                  | Description                             |
| ---------------- | --------------------- | --------------------------------------- |
| id               | UUID                  | Primary key                             |
| job_type         | VARCHAR               | Handler identifier                      |
| payload          | JSONB                 | Job input                               |
| status           | VARCHAR               | Current state                           |
| priority         | INTEGER               | Scheduling priority                     |
| run_at           | TIMESTAMPTZ           | Initial execution time                  |
| available_at     | TIMESTAMPTZ           | Earliest eligible execution             |
| attempts         | INTEGER               | Started attempts in current retry cycle |
| max_attempts     | INTEGER               | Maximum attempts                        |
| result           | JSONB                 | Successful result                       |
| last_error       | TEXT                  | Latest error                            |
| locked_by        | VARCHAR, nullable     | Current worker                          |
| lease_expires_at | TIMESTAMPTZ, nullable | Ownership expiry                        |
| lease_token      | BIGINT                | Fencing token                           |
| created_at       | TIMESTAMPTZ           | Creation timestamp                      |
| updated_at       | TIMESTAMPTZ           | Update timestamp                        |

Use database constraints where appropriate.

## 8.2 job_attempts

| Column         | Type                  | Description       |
| -------------- | --------------------- | ----------------- |
| id             | UUID                  | Primary key       |
| job_id         | UUID                  | Foreign key       |
| attempt_number | INTEGER               | Attempt number    |
| worker_id      | VARCHAR               | Worker identifier |
| status         | VARCHAR               | Attempt state     |
| started_at     | TIMESTAMPTZ           | Start time        |
| finished_at    | TIMESTAMPTZ, nullable | Completion time   |
| error_message  | TEXT, nullable        | Error details     |
| result         | JSONB, nullable       | Attempt result    |
| lease_token    | BIGINT                | Ownership token   |

Attempt statuses:

- RUNNING.
- SUCCEEDED.
- FAILED.
- ABANDONED.

Each claim that starts a new execution attempt must have a corresponding attempt record.

## 8.3 idempotency_keys

| Column          | Type        | Description             |
| --------------- | ----------- | ----------------------- |
| id              | UUID        | Primary key             |
| idempotency_key | VARCHAR     | Client key              |
| request_hash    | VARCHAR     | Normalized request hash |
| job_id          | UUID        | Associated job          |
| created_at      | TIMESTAMPTZ | Creation time           |

Enforce a unique constraint on the idempotency key within the defined scope.

## 8.4 Indexes

Add indexes based on actual query patterns:

- Job status and available_at.
- Priority and creation time for eligible jobs.
- Lease expiration for recovery.
- Job attempt lookup by job_id.
- Unique idempotency key.

Validate important query plans using PostgreSQL `EXPLAIN`.

---

# 9. API Requirements

The following three endpoints are mandatory.

## 9.1 POST /jobs

Creates a job.

Example request:

```http
POST /jobs
Content-Type: application/json
Idempotency-Key: customer-sync-101
```

```json
{
  "job_type": "partner_api_call",
  "payload": {
    "customer_id": 101,
    "operation": "sync_customer"
  },
  "priority": 5,
  "run_at": "2026-11-20T10:00:00Z"
}
```

Expected response:

```json
{
  "job_id": "uuid",
  "status": "QUEUED"
}
```

Requirements:

- Validate payload.
- Validate job type.
- Apply defaults.
- Handle idempotency.
- Persist transactionally.
- Return success only after commit.

## 9.2 GET /jobs/{id}

Retrieves job details.

Example response:

```json
{
  "job_id": "uuid",
  "job_type": "partner_api_call",
  "status": "SUCCEEDED",
  "priority": 5,
  "attempts": 2,
  "max_attempts": 5,
  "result": {
    "message": "Partner operation completed"
  },
  "last_error": null
}
```

Return HTTP 404 if the job does not exist.

## 9.3 GET /stats

Returns operational statistics.

Example response:

```json
{
  "total_jobs": 1000,
  "queued_jobs": 120,
  "running_jobs": 15,
  "retrying_jobs": 30,
  "succeeded_jobs": 800,
  "failed_jobs": 35,
  "expired_leases": 2,
  "oldest_queued_job_age_seconds": 120
}
```

These numbers are illustrative.

Statistics must be calculated from PostgreSQL.

## 9.4 POST /jobs/{id}/retry

Additional operator endpoint for manually retrying a FAILED job.

Return appropriate error responses for invalid state transitions or nonexistent jobs.

Document the endpoint in Swagger UI.

---

# 10. Worker and Lease Configuration

Use configurable initial defaults.

| Setting                    | Initial Default |
| -------------------------- | --------------: |
| Lease duration             |      60 seconds |
| Handler execution timeout  |      30 seconds |
| Lease renewal interval     |      15 seconds |
| HTTP connect timeout       |       5 seconds |
| HTTP read timeout          |      20 seconds |
| Graceful shutdown period   |      35 seconds |
| Worker polling interval    |        1 second |
| Maximum execution attempts |               5 |

These are initial engineering defaults and must be configurable through environment variables.

Validate the relationship between lease duration, handler timeout, and renewal interval.

Do not treat these values as universally correct for every partner API.

Use PostgreSQL database time for ownership expiration and recovery decisions.

## 10.1 Lease Renewal

A worker must renew its lease while processing a valid long-running job.

The renewal operation must verify current ownership and fencing token.

If renewal fails because ownership has been lost, the worker must not finalize the job.

## 10.2 Graceful Shutdown

On SIGTERM:

1. Stop polling.
2. Stop claiming new jobs.
3. Allow active handlers to finish within the configured grace period.
4. Persist completed outcomes if ownership remains valid.
5. If grace expires, stop local execution where possible.
6. Do not falsely mark unfinished jobs as successful.
7. Let unfinished ownership expire naturally for recovery.

Do not assume cancelling a local HTTP request always cancels the remote partner operation.

---

# 11. Retry and Failure Handling

## 11.1 Retryable Errors

Examples:

- Network timeout.
- Connection failure.
- HTTP 429.
- HTTP 500.
- HTTP 502.
- HTTP 503.
- HTTP 504.

## 11.2 Permanent Errors

Examples:

- Invalid payload.
- Unsupported job type.
- Missing mandatory fields.
- Invalid business operation.
- Non-retryable client errors.

## 11.3 Exponential Backoff

Initial example:

| Attempt |      Delay |
| ------- | ---------: |
| 1       |  2 seconds |
| 2       |  4 seconds |
| 3       |  8 seconds |
| 4       | 16 seconds |

Include:

- Random jitter.
- Configurable maximum delay.
- Retry-After support.
- Persisted next execution time.

HTTP 429 responses must respect Retry-After where available.

Do not count a wait period as another execution attempt.

## 11.4 Poison Job Handling

A poison job is a job that consistently crashes every worker that attempts to process it.

Required behavior:

1. Each successful claim creates an attempt record.
2. Worker crash leaves the job RUNNING until lease expiration.
3. Recovery marks the abandoned attempt as ABANDONED.
4. The job becomes eligible for another attempt if attempts remain.
5. After five started attempts, the job transitions to FAILED.
6. It must not be automatically reclaimed after terminal failure.
7. Operators must be able to inspect its complete history.
8. Manual retry must start a new retry cycle without deleting historical attempts.

Build a dedicated poison-job test.

---

# 12. Idempotency and Delivery Guarantees

## 12.1 Submission Idempotency

For a repeated submission with the same key and same normalized request:

| Original job state | Expected behavior                                |
| ------------------ | ------------------------------------------------ |
| QUEUED             | Return same job ID                               |
| RUNNING            | Return same job ID                               |
| RETRYING           | Return same job ID                               |
| SUCCEEDED          | Return same job ID and existing result           |
| FAILED             | Return same job ID; do not automatically restart |

If the same key is reused with different request content, return HTTP 409.

## 12.2 Execution Idempotency

The system guarantees at-least-once execution, not exactly-once execution.

A job may execute multiple times.

Handlers must therefore be idempotent wherever possible.

For external operations, use a stable business-operation idempotency key if the partner supports it.

Database fencing protects job state, but cannot by itself prevent duplicate external side effects.

Clearly document this limitation in README and LIMITATIONS.md.

---

# 13. Partner API Integration

The project must include a job handler that calls the mentor-provided mock partner API.

The mock API may be slow and randomly fail.

Implement:

- HTTP request execution.
- Connect and read timeouts.
- Error classification.
- Retryable error handling.
- HTTP 429 handling.
- Structured results.
- Stable external idempotency key where supported.
- Logging of request outcomes without exposing sensitive data.

The partner integration must be configurable using environment variables.

Do not hardcode credentials, URLs, or secrets.

Provide a mock partner adapter for local testing if the real API is not yet available.

The mock adapter must simulate:

- Successful requests.
- Random transient failures.
- Slow responses.
- HTTP 429 responses.
- Timeouts.

Keep production and test behavior clearly separated.

---

# 14. Observability and Logging

Use structured logging.

Include these identifiers whenever applicable:

- job_id.
- attempt_id.
- worker_id.
- lease_token.
- event timestamp.

Log the following events:

- Job submitted.
- Job claimed.
- Attempt started.
- Attempt succeeded.
- Attempt failed.
- Retry scheduled.
- Lease renewed.
- Lease expired.
- Job recovered.
- Job permanently failed.
- Manual retry requested.
- Worker started.
- Worker shutting down.

Do not log secrets or sensitive payload contents.

The `/stats` endpoint must expose the core operational metrics.

---

# 15. Testing Requirements

Testing is a mandatory product requirement.

The system must not be considered complete merely because the API endpoints work.

## 15.1 Unit Tests

Test:

- Request validation.
- State transitions.
- Retry delay calculation.
- Failure classification.
- Idempotency request hashing.
- Handler registry.
- Payload validation.
- Configuration validation.

## 15.2 Integration Tests

Test:

- Job creation.
- Job retrieval.
- Statistics.
- Duplicate submissions.
- Conflicting idempotency keys.
- Transaction rollback.
- Scheduled jobs.
- Priority ordering.
- Multiple worker claiming.
- Retry persistence.
- Manual retry.
- Lease renewal.
- Lease expiration.
- Recovery.

Use a real PostgreSQL test database rather than relying only on SQLite.

## 15.3 Concurrency Tests

Start multiple workers against the same PostgreSQL instance.

Verify:

- The same job is not claimed by multiple active owners.
- Different jobs can be processed concurrently.
- Idempotency constraints work under concurrent requests.
- Job state remains consistent.
- Stale workers cannot overwrite newer ownership.

## 15.4 Chaos Testing

Intentionally introduce failures into the running system.

Required scenarios:

1. Kill a worker immediately after claiming a job.
2. Kill a worker during handler execution.
3. Kill a worker during an external API request.
4. Kill a worker after the partner operation succeeds but before database completion.
5. Interrupt lease renewal.
6. Send SIGTERM during active execution.
7. Simulate repeated worker crashes for a poison job.

Verify:

- Accepted jobs remain persisted.
- Expired leases are recovered.
- Abandoned attempts are recorded.
- Retry limits are respected.
- Poison jobs eventually become FAILED.
- Stale workers cannot overwrite newer results.
- Healthy jobs continue processing.
- External duplicate side effects are handled according to documented idempotency support.

At least one test must launch a worker in a subprocess and terminate it during execution.

## 15.5 Soak Testing

Run thousands of jobs through multiple concurrent workers over an extended period.

Initial target:

- At least 10,000 submitted jobs.
- At least 4 concurrent workers.
- Random transient failures.
- Random worker termination and restart.
- Multiple retry cycles where applicable.

Measure:

- Accepted job count.
- Persisted job count.
- Successful job count.
- Permanently failed job count.
- Total attempts.
- Retry count.
- Expired lease count.
- Processing duration.
- PostgreSQL resource usage.
- Worker memory usage.

Compare database records against the mock partner API's received operations.

Every accepted job must remain accounted for.

Investigate any difference between expected and actual external operations.

Provide a reproducible soak-test command and a written test report.

---

# 16. Frontend and User Interface Scope

The initial product is backend-first.

Do not spend time building a complex frontend dashboard.

Required operator visibility must be available through:

- REST API.
- Swagger UI.
- Structured logs.
- Database inspection.
- `/stats` endpoint.

A simple optional CLI or lightweight admin interface may be added only after all backend reliability requirements are satisfied.

Do not let frontend work delay the queue's correctness and testing.

---

# 17. Non-Functional Requirements

## NFR-01: Durability

An acknowledged job must be committed to PostgreSQL.

## NFR-02: Concurrency

Multiple workers must process independent jobs concurrently.

## NFR-03: Reliability

Worker crashes must not permanently lose accepted jobs.

## NFR-04: Recoverability

Expired leases must be detected and recovered.

## NFR-05: Consistency

All ownership and state transitions must be validated transactionally.

## NFR-06: Maintainability

Use clear modules, type hints, documentation, and separation of responsibilities.

## NFR-07: Security

- Validate incoming payloads.
- Use environment variables for secrets.
- Do not execute arbitrary payload code.
- Avoid logging credentials.
- Use safe SQLAlchemy queries.
- Protect operator-only actions appropriately.

## NFR-08: Code Quality

- Ruff must pass.
- mypy must pass.
- No inline type-check suppressions.
- Use Python type hints.
- Use clean exception handling.
- Avoid unnecessary abstractions.

---

# 18. Project Structure

Use the following proposed structure.

```text
durable-job-queue/
│
├── app/
│   ├── api/
│   │   ├── routes/
│   │   └── schemas/
│   │
│   ├── core/
│   │   ├── config.py
│   │   └── logging.py
│   │
│   ├── db/
│   │   ├── models/
│   │   ├── session.py
│   │   └── base.py
│   │
│   ├── queue/
│   │   ├── claiming.py
│   │   ├── retry.py
│   │   ├── recovery.py
│   │   ├── lease.py
│   │   └── state.py
│   │
│   ├── workers/
│   │   ├── worker.py
│   │   └── lifecycle.py
│   │
│   ├── handlers/
│   │   ├── registry.py
│   │   └── partner_api.py
│   │
│   └── main.py
│
├── alembic/
│
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── concurrency/
│   ├── chaos/
│   └── soak/
│
├── scripts/
│
├── DESIGN.md
├── PRD.md
├── LIMITATIONS.md
├── README.md
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── alembic.ini
├── .env.example
└── .gitignore
```

The structure may be refined when necessary, but responsibilities must remain clearly separated.

---

# 19. Environment Configuration

Create `.env.example` containing documented configuration variables.

At minimum:

```env
APP_ENV=development

DATABASE_URL=postgresql+psycopg://postgres:postgres@db:5432/job_queue

WORKER_ID=worker-1
WORKER_POLL_INTERVAL=1

LEASE_DURATION_SECONDS=60
LEASE_RENEWAL_INTERVAL_SECONDS=15
HANDLER_TIMEOUT_SECONDS=30
GRACEFUL_SHUTDOWN_SECONDS=35

HTTP_CONNECT_TIMEOUT_SECONDS=5
HTTP_READ_TIMEOUT_SECONDS=20

MAX_JOB_ATTEMPTS=5
RETRY_BASE_DELAY_SECONDS=2
RETRY_MAX_DELAY_SECONDS=60

PARTNER_API_BASE_URL=
PARTNER_API_KEY=
```

Validate configuration at startup.

Never commit real secrets.

---

# 20. Deployment Requirements

The complete system must run using:

```bash
docker compose up
```

Docker Compose must start:

- PostgreSQL.
- FastAPI API server.
- At least one independent worker.

The architecture must allow multiple worker replicas.

Provide health checks and reliable startup dependencies.

Database migrations must be applied before application components rely on the schema.

Do not rely on container startup order alone as proof that PostgreSQL is ready.

Provide clear startup and shutdown logs.

---

# 21. Documentation Deliverables

Antigravity must create and maintain:

## README.md

Include:

- Project overview.
- Architecture.
- Prerequisites.
- Installation.
- Environment configuration.
- Database migration instructions.
- Running with Docker Compose.
- API documentation.
- Running workers.
- Running tests.
- Running chaos tests.
- Running soak tests.
- Example job submission.
- Example job status retrieval.

## DESIGN.md

Document:

- Architecture decisions.
- Database design.
- Job lifecycle.
- Claiming mechanism.
- Lease management.
- Fencing tokens.
- Retry policy.
- Idempotency.
- Delivery guarantees.
- Assumptions.
- Rejected alternatives.
- Open questions.

## LIMITATIONS.md

Document:

- At-least-once execution limitations.
- Possible duplicate external operations.
- PostgreSQL dependency.
- Priority starvation.
- Long-running handler limitations.
- External API dependency.
- Retry limitations.
- Operational assumptions.

---

# 22. Development Phases

Antigravity must implement the project incrementally.

Do not attempt to build the entire system in one unreviewed operation.

## Phase 0: Repository Inspection and Planning

- Inspect existing files.
- Identify existing code and configuration.
- Do not overwrite useful existing work.
- Review the PRD and DESIGN.md.
- Create an implementation plan.
- Identify unresolved design decisions.

**Deliverable:** Implementation plan.

## Phase 1: Foundation and Database

- Set up Python project.
- Configure FastAPI.
- Configure PostgreSQL.
- Configure SQLAlchemy.
- Configure Alembic.
- Create database models.
- Create initial migration.
- Add database health checks.

**Acceptance:** Database starts, migration works, and models can persist and retrieve records.

## Phase 2: API Implementation

Implement:

- POST /jobs.
- GET /jobs/{id}.
- GET /stats.

Add:

- Pydantic validation.
- Error handling.
- Swagger documentation.
- Transaction-safe job creation.

**Acceptance:** All mandatory endpoints work with PostgreSQL integration tests.

## Phase 3: Atomic Job Claiming

- Create independent worker process.
- Implement polling.
- Implement atomic claiming.
- Use FOR UPDATE SKIP LOCKED.
- Assign worker ID.
- Increment fencing token.
- Create attempt records.

**Acceptance:** Multiple workers cannot simultaneously own the same job.

Do not proceed to complex handler execution until the claim mechanism is tested.

## Phase 4: Handler Execution

- Implement handler registry.
- Implement a basic test handler.
- Execute handlers outside claim transactions.
- Persist results.
- Handle execution exceptions.

**Acceptance:** Jobs move from QUEUED to RUNNING to SUCCEEDED or a documented failure state.

## Phase 5: Lease and Recovery

- Implement lease expiration.
- Implement lease renewal.
- Implement recovery process.
- Implement fencing validation.
- Mark abandoned attempts.
- Prevent stale updates.

**Acceptance:** A crashed worker's job becomes recoverable and stale workers cannot overwrite newer ownership.

## Phase 6: Retry System

- Implement error classification.
- Implement exponential backoff.
- Add jitter.
- Add Retry-After support.
- Enforce maximum attempts.
- Implement terminal FAILED state.
- Implement manual retry.

**Acceptance:** Retryable failures retry correctly; permanent failures do not retry unnecessarily.

## Phase 7: Idempotency

- Implement idempotency keys.
- Implement normalized request hashing.
- Add unique database constraints.
- Handle concurrent duplicate requests.
- Return HTTP 409 for conflicting request content.

**Acceptance:** Repeated submissions cannot create duplicate jobs within the defined idempotency scope.

## Phase 8: Partner API Integration

- Implement partner API handler.
- Configure timeouts.
- Implement failure classification.
- Test random failures.
- Test slow responses.
- Test rate limiting.

**Acceptance:** Partner operations work through the actual job queue.

## Phase 9: Reliability and Observability

- Improve `/stats`.
- Add structured logging.
- Add worker lifecycle handling.
- Implement SIGTERM behavior.
- Add poison-job handling.
- Complete operator visibility.

**Acceptance:** Operators can understand system state and investigate failures.

## Phase 10: Reliability Testing

- Unit tests.
- Integration tests.
- Concurrency tests.
- Chaos tests.
- Poison-job tests.
- Soak tests.

**Acceptance:** The documented reliability guarantees have reproducible test evidence.

## Phase 11: Finalization

- Complete README.md.
- Complete DESIGN.md.
- Complete LIMITATIONS.md.
- Complete `.env.example`.
- Configure CI.
- Run Ruff.
- Run mypy.
- Run pytest.
- Verify Docker Compose startup.
- Prepare a demonstration.

**Acceptance:** The system is ready for mentor review and live walkthrough.

---

# 23. Definition of Done

The product is complete only when all of the following are satisfied.

### Core Functionality

- [ ] Jobs can be submitted.
- [ ] Jobs are durably persisted.
- [ ] Job status can be retrieved.
- [ ] Job results can be retrieved.
- [ ] Statistics are available.
- [ ] Priority scheduling works.
- [ ] Scheduled execution works.

### Worker System

- [ ] Multiple workers can run concurrently.
- [ ] Atomic claiming works.
- [ ] Workers execute registered handlers.
- [ ] Worker ownership is tracked.
- [ ] Lease renewal works.
- [ ] Lease expiration works.
- [ ] Crash recovery works.
- [ ] Fencing prevents stale updates.
- [ ] Graceful shutdown works.

### Failure Management

- [ ] Retryable failures are retried.
- [ ] Permanent failures are handled.
- [ ] Exponential backoff works.
- [ ] Retry-After is respected.
- [ ] Maximum attempts are enforced.
- [ ] Poison jobs eventually become FAILED.
- [ ] Manual retry works.
- [ ] Attempt history is retained.

### Idempotency

- [ ] Duplicate submissions return the original job.
- [ ] Conflicting idempotency keys return HTTP 409.
- [ ] Concurrent duplicate submissions are safe.
- [ ] Execution idempotency limitations are documented.

### Testing

- [ ] Unit tests pass.
- [ ] Integration tests pass.
- [ ] Concurrency tests pass.
- [ ] Chaos tests pass.
- [ ] Poison-job tests pass.
- [ ] Soak tests are reproducible.
- [ ] PostgreSQL and partner API results are compared.

### Engineering Quality

- [ ] Ruff passes.
- [ ] mypy passes without inline suppressions.
- [ ] Docker Compose works.
- [ ] Migrations work.
- [ ] README is complete.
- [ ] DESIGN.md is complete.
- [ ] LIMITATIONS.md is complete.
- [ ] No credentials are committed.

---

# 24. Instructions for Antigravity

Treat this PRD as the primary product requirements document.

Follow these rules throughout implementation:

1. Do not replace PostgreSQL with another queue or broker.
2. Do not introduce Celery, RQ, Redis, or Kafka.
3. Do not claim exactly-once execution.
4. Do not hold database transactions open during external API calls.
5. Do not skip lease recovery or fencing-token validation.
6. Do not silently ignore worker crashes.
7. Do not remove historical attempt records.
8. Do not use in-memory job storage as the source of truth.
9. Do not hardcode secrets.
10. Do not use SQLite as a substitute for PostgreSQL in reliability tests.
11. Do not skip tests to make the implementation appear complete.
12. Do not create unnecessary abstractions or over-engineer the initial version.
13. Keep code modular, readable, typed, and maintainable.
14. Update documentation whenever implementation decisions change.
15. Explain important architectural decisions before making significant changes.

**Important implementation workflow:**

- Start by inspecting the repository.
- Read PRD.md and DESIGN.md.
- Present a concise implementation plan.
- Implement one phase at a time.
- Run relevant tests after each phase.
- Report completed work, test results, and remaining issues.
- Do not proceed past the API and atomic claim loop without validating those foundations.
- Ask for clarification when a requirement conflicts with the mentor's specification.
- Never report a test as passing unless it was actually executed.

The final implementation must be understandable enough for the developer to explain its architecture, modify its behavior during a live walkthrough, and defend its reliability guarantees.

---

**Final Product Objective**

Build a PostgreSQL-backed durable job queue that demonstrates safe concurrent processing, recoverability after worker crashes, controlled retries, idempotency, and measurable reliability through chaos and soak testing.
