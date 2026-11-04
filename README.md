# AI Trading Assistant

An institutional-grade trading assistant, built from scratch one verified vertical slice at a time. The focus is **robustness, risk management, and statistical validity** — not guaranteed profitability. Every layer is deterministic and tamper-evident so that a run can be replayed and audited exactly.

> **Status:** early. The core is a deterministic paper-trading research system, not financial advice and not a live trading system.

## Design principles

- **Determinism.** No wall-clock reads, no randomness. The caller supplies every timestamp, so replaying a run reproduces an identical lineage chain, the same fills, and the same reconciliation result.
- **Money is `Decimal`, never `float`.** No binary-float rounding in any price, quantity, or cash figure.
- **Immutable value types.** Core records are frozen and slotted — a run can be inspected but not rewritten after the fact.
- **Fail-closed gates.** Risk/compliance checks reject on error; nothing reaches the broker unless a check explicitly approves it.
- **Single choke point.** Every order takes one mandatory path: `intent -> gates -> broker -> book -> lineage`.
- **Tamper-evident audit trail.** Each action is appended to a sha256 hash chain that can be verified end-to-end.

## Completed-run journal

A completed `DemoResult` can be persisted with `persist_demo_result(result, run_id=..., store=...)`. It uses the same JSON-safe serializer as the HTTP response and records every order outcome, gate report, lineage reference, reconciliation value, and run summary. The caller supplies the run ID; identical retries are idempotent, while conflicting reuse is rejected.

```python
from ledgerline.demo import run_demo
from ledgerline.storage import SQLiteLedgerStore, persist_demo_result

result = run_demo()
store = SQLiteLedgerStore("./data/ledgerline.sqlite3")
record = persist_demo_result(result, run_id="research-run-001", store=store)
assert store.verify()
```

`RunStore` is the adapter contract. SQLite is for local development and tests only. Vercel serverless filesystems are ephemeral, so SQLite there is not production-durable. The managed PostgreSQL adapter design, transaction algorithm, schema, security model, and rollout gates are in [`docs/managed-postgres-adapter.md`](docs/managed-postgres-adapter.md). No database provider or production resource is configured by this change.

## Getting started

Requires **Python 3.12+**. The PostgreSQL adapter uses Psycopg 3, declared as a runtime dependency; `pytest` is the development dependency.

```bash
python -m pip install -e ".[dev]"
python -m pytest
PYTHONPATH=src python -m ledgerline.demo
```

## Disclaimer

This software is for research and educational purposes only. It is **not** financial advice and comes with **no warranty**. Trading involves substantial risk of loss. Do not use it to trade real money without independent review.
