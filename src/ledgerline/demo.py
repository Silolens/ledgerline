"""End-to-end vertical slice: bars -> signal -> intent -> choke point -> book.

This ties every layer built so far into one runnable path and proves the core
invariants hold when they are composed:

    synthetic bars                     (data)
      -> MACrossSignal.update          (signal)
      -> OrderIntent per event         (intent)
      -> ChokePoint.submit             (gates -> broker -> book -> lineage)
      -> reconcile(book, broker.fills) (post-trade check)
      -> lineage.verify()              (tamper-evident audit trail)

It is fully deterministic: no wall-clock, no randomness. The caller supplies
every timestamp (here, the bar timestamps), so replaying ``run_demo`` reproduces
an identical lineage chain, the same fills, and the same reconciliation result.

Run it directly::

    python -m ledgerline.demo
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping

from ledgerline.book.book import Book
from ledgerline.core.types import Numeric, OrderIntent
from ledgerline.execution.choke_point import ChokePoint, ChokeResult
from ledgerline.execution.paper_broker import PaperBroker
from ledgerline.gates.gates import BuyingPowerGate, ComplianceGate, GateChain
from ledgerline.lineage.lineage import LineageLog
from ledgerline.reconcile.reconciliation import ReconciliationReport, reconcile
from ledgerline.signal.bars import synthetic_bars
from ledgerline.signal.ma_cross import MACrossSignal

__all__ = ["DemoResult", "run_demo"]


@dataclass(frozen=True, slots=True)
class DemoResult:
    """Everything the slice produced, for assertions and reporting.

    Frozen so a caller (a test, the ``__main__`` block) can inspect the run
    without being able to rewrite what happened.
    """

    results: tuple[ChokeResult, ...]
    reconciliation: ReconciliationReport
    lineage_intact: bool

    @property
    def filled(self) -> tuple[ChokeResult, ...]:
        return tuple(r for r in self.results if r.filled)

    @property
    def clean(self) -> bool:
        """True when the book reconciles AND the audit chain verifies."""
        return self.reconciliation.clean and self.lineage_intact


def run_demo(
    *,
    symbol: str = "AAPL",
    closes: tuple[Numeric, ...] = ("10", "10", "10", "20", "20", "10", "10"),
    quantity: Numeric = "10",
    starting_cash: Numeric = "100000",
    fast: int = 2,
    slow: int = 3,
    reference_prices: Mapping[str, Numeric] | None = None,
) -> DemoResult:
    """Run one full pass of the vertical slice and return its outcome.

    ``closes`` is crafted so the fast MA crosses above the slow MA (a BUY) and
    later back below it (a SELL), exercising both signal directions. Each signal
    becomes a marketable :class:`OrderIntent` priced at the bar's close, routed
    through the choke point so gates, broker, book and lineage all engage.

    ``reference_prices`` optionally supplies a per-symbol mark to the paper
    broker. When given, the broker runs its marketability check against that
    mark (BUY fills when mark <= limit, SELL when mark >= limit) and fills at
    the mark — giving deterministic price improvement — while a non-marketable
    intent is broker-rejected without touching the book. When omitted, the
    broker fills every gate-approved intent at its own limit, so the slice
    stays self-contained (no separate market-data feed needed).
    """
    starting = Decimal(str(starting_cash))

    # --- wire the layers ---------------------------------------------------
    book = Book(starting)
    lineage = LineageLog()
    gates = GateChain([ComplianceGate(), BuyingPowerGate()])
    broker = PaperBroker(reference_prices=reference_prices)
    choke = ChokePoint(gate_chain=gates, broker=broker, book=book, lineage=lineage)

    # --- data -> signal ----------------------------------------------------
    bars = synthetic_bars(symbol, closes)
    close_at = {b.timestamp: b.close for b in bars}
    events = MACrossSignal(fast=fast, slow=slow).update(bars)

    # --- signal -> intent -> choke point -----------------------------------
    results: list[ChokeResult] = []
    for event in events:
        # Price the order at the close that produced the crossover. A BUY at the
        # close is marketable (fills at limit); a SELL likewise. Real execution
        # would offset by a slippage/limit band; that is an execution-layer
        # concern, not the slice's.
        intent = OrderIntent(
            event.symbol,
            event.side,
            quantity,
            close_at[event.timestamp],
        )
        results.append(choke.submit(intent, timestamp=event.timestamp))

    # --- post-trade checks -------------------------------------------------
    report = reconcile(book, broker.fills, starting_cash=starting)
    return DemoResult(
        results=tuple(results),
        reconciliation=report,
        lineage_intact=lineage.verify(),
    )


def _format(result: DemoResult) -> str:
    lines = ["=== AI Trading Assistant — end-to-end slice ===", ""]
    for r in result.results:
        i = r.intent
        px = r.execution.fill.price if (r.execution and r.execution.fill) else "-"
        lines.append(
            f"  {r.outcome.value:<14} {i.side.value:<4} {i.quantity} {i.symbol} "
            f"@ limit {i.limit_price} -> fill {px}"
        )
    rec = result.reconciliation
    lines += [
        "",
        f"  signals -> orders : {len(result.results)}",
        f"  fills applied     : {len(result.filled)}",
        f"  starting cash     : {rec.starting_cash}",
        f"  book cash         : {rec.book_cash}",
        f"  expected cash     : {rec.expected_cash}",
        f"  cash drift        : {rec.cash_drift}",
        f"  reconciled clean  : {rec.clean}",
        f"  lineage verified  : {result.lineage_intact}",
        "",
        f"  RESULT: {'OK' if result.clean else 'DRIFT DETECTED'}",
    ]
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover
    outcome = run_demo()
    print(_format(outcome))
    raise SystemExit(0 if outcome.clean else 1)