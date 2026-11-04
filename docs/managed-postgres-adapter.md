# Managed PostgreSQL adapter

## Status

`PostgresLedgerStore` implements the same `RunStore` contract as SQLite. It is provider-neutral and accepts a connection factory. Psycopg 3 is declared as a required runtime dependency in `pyproject.toml`, so Vercel's Python build installs the driver. No provider, database, credential, Vercel environment variable, or production resource has been provisioned.

## Schema and migration

Apply `src/ledgerline/storage/migrations/0001_ledger_runs.sql` with a dedicated migration role. The schema stores JSONB for querying and `payload_canonical` for exact hash verification. A singleton `ledger_chain_head` row holds the current chain tip. Sequence values are assigned under a transaction lock rather than relying on a PostgreSQL identity sequence, so committed rows remain contiguous even when transactions roll back.

## Atomic append

The adapter starts a transaction, takes a transaction-scoped advisory lock, checks run-ID idempotency, locks the singleton head, computes the same SHA-256 envelope as SQLite, inserts the row and advances the head in the same transaction. A failed transaction rolls back both row and head. The unique run ID and record hash constraints are additional guards. Multiple Vercel instances serialize through PostgreSQL, not process-local locks.

## Vercel connection configuration

Provide a fresh-connection factory using psycopg and the provider's pooled/serverless endpoint. Set bounded connect/statement/transaction timeouts and TLS certificate verification. Do not create an unbounded per-invocation pool. Keep `DATABASE_URL` server-only and never expose it with a `NEXT_PUBLIC_` prefix. Run migrations out of band; never create schema on a request path. Missing database configuration must fail closed if persistence is requested. There is no SQLite fallback on Vercel.

## Runtime permissions and integrity

The runtime role should have `SELECT`/`INSERT` on `ledger_runs` and only the required head-row permissions on `ledger_chain_head`; it must not have `UPDATE`, `DELETE`, `TRUNCATE`, DDL, or trigger-management rights on journal rows. Migration ownership must be separate. Database triggers reject UPDATE and DELETE. Hash chaining is tamper-evident, not absolute immutability: a database owner can still bypass controls, so periodically anchor the latest hash outside the database.

## Validation and rollout

Run `pytest` for local SQLite coverage. Set `LEDGERLINE_TEST_DATABASE_URL` to a disposable migrated PostgreSQL database and install `.[dev]` to run PostgreSQL integration tests, including concurrent appends. Before preview deployment, add tests for connection loss, transaction rollback, restore, multi-instance contention, and role permissions. Only after those pass should preview credentials be configured. Production provisioning and deployment require separate explicit approval.

The unauthenticated `/api/run` remains read-only. Do not wire writes into it until authentication, authorization, rate limits, run ownership and API audit controls are implemented.
