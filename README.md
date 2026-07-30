# AI Trading Assistant

An institutional-grade trading assistant, built from scratch one verified vertical
slice at a time. The focus is **robustness, risk management, and statistical
validity** — not guaranteed profitability. Every layer is deterministic and
tamper-evident so that a run can be replayed and audited exactly.

> **Status:** early. One full vertical slice is wired end-to-end and covered by
> 147 passing tests. This is a research/engineering project, **not financial
> advice and not a live trading system.**

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
  `intent -> gates -> broker -> book -> lineage`. There is no side door.
- **Tamper-evident audit trail.** Each action is appended to a sha256 hash chain
  that can be verified end-to-end.

## The vertical slice

The current slice ties every layer built so far into one runnable, deterministic path:

```text
synthetic bars                      (data)
  -> MACrossSignal.update           (signal: MA-crossover -> BUY/SELL events)
  -> OrderIntent per event          (intent)
  -> ChokePoint.submit              (gates -> broker -> book -> lineage)
  -> reconcile(book, broker.fills)  (post-trade: rebuild expected state from fills)
  -> lineage.verify()              (audit: hash chain intact)
```

### Components

| Layer       | Module                              | Responsibility |
|-------------|-------------------------------------|----------------|
| Data        | `ledgerline.signal.bars`               | OHLCV `Bar`, `synthetic_bars` helper |
| Signal      | `ledgerline.signal.ma_cross`           | `MACrossSignal` emits BUY/SELL on MA crossover |
| Gates       | `ledgerline.gates.gates`               | `ComplianceGate` (deny-list), `BuyingPowerGate`, `GateChain` |
| Execution   | `ledgerline.execution.paper_broker`    | `PaperBroker` — deterministic fills, marketability check |
| Execution   | `ledgerline.execution.choke_point`     | `ChokePoint` — the single mandatory order path |
| Book        | `ledgerline.book.book`                 | Positions (avg-cost lots), signed cash, `snapshot()` |
| Reconcile   | `ledgerline.reconcile.reconciliation`  | Rebuilds expected state from fills alone, reports drift |
| Lineage     | `ledgerline.lineage.lineage`           | Append-only sha256 hash chain, `verify()` |
| Demo        | `ledgerline.demo`                      | `run_demo()` — the whole slice, end-to-end |

### Broker fill model

The `PaperBroker` has three deterministic branches:

- **No reference price** → fills at the order's own limit (self-contained; no market feed needed).
- **Reference price, marketable** → fills *at the reference mark* (price improvement).
  BUY is marketable when `mark <= limit`; SELL when `mark >= limit`.
- **Reference price, not marketable** → broker-rejected; the book is never touched.

## Getting started

Requires **Python 3.12+**. No third-party runtime dependencies (stdlib only);
`pytest` is the sole dev dependency.

```bash
# from the project root
python -m pytest            # run the full suite (147 tests)

# run the end-to-end demo
# Windows PowerShell:
$env:PYTHONPATH="src"; python -m ledgerline.demo
# macOS / Linux:
PYTHONPATH=src python -m ledgerline.demo
```

The demo prints each order's outcome, the reconciliation summary, and whether the
lineage chain verified, then exits `0` on a clean run.

## Project layout

```text
src/ledgerline/        # the package (src-layout; pytest puts src/ on the path)
  book/             # positions + cash
  core/             # shared value types (Side, OrderIntent, Decimal coercion)
  execution/        # paper broker + choke point
  gates/            # compliance + buying-power risk gates
  lineage/          # tamper-evident audit chain
  reconcile/        # post-trade reconciliation
  signal/           # bars + MA-crossover signal
  demo.py           # end-to-end runnable slice
tests/              # pytest suite (mirrors the package)
pyproject.toml      # build + pytest config
```

## Roadmap

Built one thin, tested vertical slice at a time. Planned directions include richer
execution microstructure, additional risk/compliance gates, portfolio-level risk,
performance monitoring, and multi-broker / multi-asset adapters.

## Disclaimer

This software is for research and educational purposes only. It is **not**
financial advice and comes with **no warranty**. Trading involves substantial risk
of loss. Do not use it to trade real money without independent review.