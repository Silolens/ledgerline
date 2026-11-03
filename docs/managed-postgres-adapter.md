# Managed PostgreSQL adapter design

## Decision

Use a managed PostgreSQL database for Vercel deployments. Keep `SQLiteLedgerStore` for local development and tests. The application depends on the `RunStore` structural contract, not on a provider SDK. A future `PostgresLedgerStore` should use psycopg 3 (optional dependency) and accept a connection factory so credentials and pooling remain outside the domain package.

No provider, database, credential, Vercel environment variable, or production resource is provisioned by this design document.

## Contract and record model

`RunStore` exposes `append_run(run_id, payload)`, `get_run(run_id)`, `records()`, and `verify()`. `persist_demo_result()` serializes a completed `DemoResult` with the existing exact-decimal JSON serializer and passes the full report to that contract. The caller supplies a stable, non-empty run ID. Repeating an identical ID and payload must be idempotent; reusing an ID for different content must raise `DuplicateRunError`.

## PostgreSQL schema (initial proposal)

```sql
CREATE TABLE ledger_runs (
    seq BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE,
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    payload_canonical TEXT NOT NULL,
    prev_hash CHAR(64) NOT NULL,
    record_hash CHAR(64) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ledger_runs_created_at_idx ON ledger_runs (created_at DESC);
```

`payload_canonical` stores the exact canonical JSON text used as hash input. Hashes must be computed over that canonical text, not PostgreSQL's JSONB reserialization. `created_at` is database metadata only; it must not enter the deterministic trading report or its hash input.

## Atomic append algorithm

1. Validate `run_id` and canonicalize the JSON-safe object using the same canonical JSON routine as SQLite.
2. Begin a transaction and acquire a transaction-scoped advisory lock dedicated to the journal chain (or lock a singleton head row). This serializes sequence assignment across concurrent Vercel instances.
3. Look up `run_id`. If present with identical canonical payload, return the existing record. If content differs, raise `DuplicateRunError`.
4. Read the last `(seq, record_hash)`; use sequence zero and the all-zero genesis hash for an empty journal.
5. Compute SHA-256 over the same canonical envelope used by SQLite: sequence, run ID, canonical payload text, and predecessor hash.
6. Insert the new record and commit. The unique constraints are the final duplicate/concurrency guard.

Do not use `MAX(seq)+1` without the transaction lock. Do not rely on a process-local mutex: Vercel may run multiple instances concurrently.

## Immutability and permissions

- Application DB role: `SELECT` and `INSERT` only; no `UPDATE`, `DELETE`, `TRUNCATE`, schema changes, or trigger bypass.
- Migration role: separate, unavailable to the runtime function.
- Add database triggers rejecting `UPDATE` and `DELETE` as defense in depth.
- Restrict database network access where supported; require TLS and verify certificates.
- Keep credentials in Vercel encrypted environment variables, never in source, logs, or client bundles.
- A database owner can still alter data or disable triggers. Hash chaining is tamper-evident, not absolute immutability; periodically anchor the latest hash outside the database for stronger independent evidence.

## Vercel runtime and failure behavior

- Use the provider's serverless/pooled PostgreSQL connection endpoint and a supported TLS connection string; avoid opening an unbounded connection pool per invocation.
- Initialize schema only through migrations, never on a request path.
- Configure explicit connection, statement, and transaction timeouts.
- Treat `DATABASE_URL` as server-only. Never prefix it with `NEXT_PUBLIC_`.
- No SQLite fallback on Vercel. Missing configuration must fail closed if persistence is requested.
- Do not add persistence to the currently unauthenticated `/api/run` route. It is intentionally a deterministic read-only demo. Any API that accepts run IDs or writes user/run state first needs authentication, authorization, input validation, rate limits, and audit ownership rules.
- Database unavailability must not silently report a persisted run. Return a clear failure to the caller; never claim success before commit.

## Verification and rollout gates

Before enabling the adapter: run the same adapter contract tests against SQLite and PostgreSQL; test concurrent appends, duplicate retries, conflicting IDs, rollback, connection loss, tamper detection, and multi-instance sequence continuity. Run migrations against a disposable database first. Add backup/restore and point-in-time recovery drills, retention policy, monitoring, and secret rotation. Only then configure preview credentials and test a preview deployment. Production provisioning and deployment require separate explicit approval.
