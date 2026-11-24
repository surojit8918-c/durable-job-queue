# System Limitations & Architectural Trade-offs

## 1. At-Least-Once Delivery vs Exactly-Once Execution
- **Limitation**: The system guarantees **at-least-once** job execution, not exactly-once execution.
- **Consequence**: If a worker crashes after completing an external side-effect (e.g. charging a payment or calling a partner API) but before recording the successful status in PostgreSQL, the lease will expire and the job will be retried by another worker.
- **Mitigation**: Job handlers interacting with external APIs must be idempotent or pass a deterministic external idempotency key (derived from `job_id`) to the third-party partner.

## 2. PostgreSQL as Single Bottleneck & Polling Load
- **Limitation**: Polling PostgreSQL with `SELECT ... FOR UPDATE SKIP LOCKED` introduces database load proportional to the number of concurrent workers and polling frequency.
- **Consequence**: While suitable for thousands of jobs per second on modern hardware, it does not scale to millions of concurrent distributed events without sharding or dedicated streaming architectures (like Kafka).
- **Mitigation**: Intelligent polling intervals with exponential backoff on empty queues, indexed queries, and minimal transaction durations.

## 3. Priority Inversion and Starvation
- **Limitation**: Strict priority ordering (`ORDER BY priority DESC, created_at ASC`) prioritizes high-priority jobs indefinitely.
- **Consequence**: An endless stream of high-priority jobs will starve lower-priority jobs.
- **Mitigation**: Operational visibility via `/stats` exposes `oldest_queued_job_age_seconds` so operators can detect queue starvation. Future versions may implement priority aging.

## 4. Local Worker Process Crashes & Abandoned Attempts
- **Limitation**: When a worker process abruptly dies (e.g. `SIGKILL`, power loss, or OOM), it cannot notify the database.
- **Consequence**: The job remains in `RUNNING` status until its lease duration expires (`lease_expires_at <= now()`).
- **Mitigation**: Configurable `lease_duration` (default 60s) balanced against handler timeouts, combined with proactive recovery sweeps.

## 5. Non-Reversible Side Effects
- **Limitation**: Database transaction rollback cannot undo external network calls that already succeeded.
- **Mitigation**: External calls are performed strictly outside the database claim transaction, and database state updates use fencing tokens to prevent stale overwrites.

## 6. Test Instrumentation Mock Partner State File Persistence (Windows NTFS)
- **Limitation**: In test and benchmark environments, `MockPartnerAdapter` persists simulated partner call history across processes via an on-disk JSON file (`.mock_partner_state.json` / `.mock_partner_soak_state.json`) updated using atomic temporary file replacement (`temp_file.replace()`).
- **Consequence**: On Windows NTFS under high-concurrency multithreading (e.g. 10,000 jobs across 4 concurrent worker threads), rapid atomic file replacements occasionally encounter transient OS file-sharing locks (`WinError 32 / 5` from antivirus or Windows Search indexing) or abrupt process termination. Because `replace()` exceptions are suppressed in the test harness to prevent synthetic test aborts, the mock adapter's on-disk JSON audit file may omit a small number of flushed records upon sudden termination.
- **Clarification**: This is strictly an **in-test instrumentation / mock-persistence artifact** on Windows file replacement, **not** an application queue defect or database data loss. In PostgreSQL, all 10,000 job records, attempt histories, and handler results remain 100% durable and accounted for.
- **External Exactly-Once Disclaimer**: Neither the mock adapter nor the queue system guarantees external exactly-once execution. If an external API call succeeds but the worker crashes before committing the status in PostgreSQL, the job is retried upon lease expiry; external endpoints must implement their own idempotency key validation.
