# AI Trading Assistant

An institutional-grade trading assistant, built from scratch one verified vertical
slice at a time. The focus is **robustness, risk management, and statistical
validity** — not guaranteed profitability. Every layer is deterministic and
tamper-evident so that a run can be replayed and audited exactly.

> **Status:** early. The core is a deterministic paper-trading research system,
> not financial advice and not a live trading system.

## Design principles

- **Determinism.** No wall-clock reads, no randomness. The caller supplies every
  timestamp, so replaying a run reproduces an identical lineage chain, the same
  fills, and the same reconciliation result.
- **Money is `Decimal`, never `float`.** No binary-float rounding in any price,
  quantity, or cash figure.
- **Immutable value types.** Core records are frozen and slotted — a run can be
  inspected but not rewritten after the fact.
- **Fail-closed gates.** Risk/compliance checks reject on error; nothing reaches
  the broker unless a check explicitly approves it.
- **Single choke point.** Every order takes one mandatory path:
  `intent -> gates -> broker -> book -> lineage`.
- **Tamper-evident audit trail.** Each action is appended to a sha256 hash chain
  that can be verified end-to-end.

## Persistent run journal (local development)

`ledgerline.storage.SQLiteLedgerStore` stores completed run snapshots in a
transactional SQLite journal. Each row is linked to its predecessor by SHA-256.
The store supports idempotent retries for an identical `run_id`, rejects reuse
of an id with different content, and verifies the full chain. Database triggers
reject ordinary UPDATE and DELETE statements.

```python
from ledgerline.storage import SQLiteLedgerStore

store = SQLiteLedgerStore("./data/ledgerline.sqlite3")
record = store.append_run("research-run-001", {
    "orders": 2,
    "lineage_intact": True,
    "clean": True,
})
assert store.verify()
```

The caller supplies `run_id`; the store does not read the wall clock or generate
random identifiers. Persist the exact JSON-safe run report, including order
outcomes, reconciliation and lineage references. This first adapter is for
local development and tests only: Vercel serverless filesystems are ephemeral,
and SQLite on them is not production-durable. A managed database adapter and
deployment configuration are a separate milestone.

## Getting started

Requires **Python 3.12+**. No third-party runtime dependencies (stdlib only);
`pytest` is the sole dev dependency.

```bash
python -m pytest
PYTHONPATH=src python -m ledgerline.demo
```

## Disclaimer

This software is for research and educational purposes only. It is **not**
financial advice and comes with **no warranty**. Trading involves substantial
risk of loss. Do not use it to trade real money without independent review.
