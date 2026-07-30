"""Reconciliation: compare the internal Book against the broker's own record.

After a trading session the internal :class:`Book` (positions, cash) must agree
with what the broker actually filled. This module rebuilds expected state from
the broker's :attr:`~ledgerline.execution.paper_broker.PaperBroker.fills` and
compares it against the live book, reporting any drift.

Design
------
* **Independent rebuild.** Expected positions and cash are computed solely from
  the broker's fill list, never from the book's own history, so the check is
  genuinely independent.
* **Decimal throughout.** All arithmetic uses Decimal; no float leaks in.
* **Immutable result.** :class:`ReconciliationReport` is frozen; callers can
  store or log it without risk of mutation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Sequence

from ledgerline.book.book import Book, Fill

__all__ = ["PositionDrift", "ReconciliationReport", "reconcile"]


@dataclass(frozen=True, slots=True)
class PositionDrift:
    """Drift for one symbol between the book and the broker's record."""

    symbol: str
    book_quantity: Decimal
    expected_quantity: Decimal

    @property
    def quantity_drift(self) -> Decimal:
        return self.book_quantity - self.expected_quantity

    @property
    def clean(self) -> bool:
        return self.quantity_drift == Decimal("0")


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """Full comparison of the book against the broker's fill record.

    ``cash_drift`` is ``book.cash - expected_cash`` (positive means the book
    holds more cash than the fills imply). ``position_drifts`` covers every
    symbol that appears in either the book or the broker fills. ``clean`` is
    True only when every drift is zero.
    """

    starting_cash: Decimal
    book_cash: Decimal
    expected_cash: Decimal
    position_drifts: tuple[PositionDrift, ...]

    @property
    def cash_drift(self) -> Decimal:
        return self.book_cash - self.expected_cash

    @property
    def clean(self) -> bool:
        return self.cash_drift == Decimal("0") and all(
            d.clean for d in self.position_drifts
        )


def reconcile(
    book: Book,
    fills: Sequence[Fill],
    *,
    starting_cash: Decimal,
) -> ReconciliationReport:
    """Compare *book* against an independent sequence of broker *fills*.

    Parameters
    ----------
    book:
        The internal book whose state is being verified.
    fills:
        The broker's own fill record (e.g. ``PaperBroker.fills``).
    starting_cash:
        The cash the book was initialised with; needed to reconstruct expected
        cash independently of the book.
    """
    # Rebuild expected state from fills alone.
    expected_cash = starting_cash
    expected_qty: dict[str, Decimal] = {}

    for fill in fills:
        sym = fill.symbol.upper()
        notional = fill.quantity * fill.price * fill.side.sign
        expected_cash -= notional
        expected_qty[sym] = expected_qty.get(sym, Decimal("0")) + fill.quantity * fill.side.sign

    # Collect every symbol that appears in either source.
    book_snap = book.snapshot()
    book_syms = {p.symbol for p in book_snap.positions}
    all_syms = book_syms | set(expected_qty.keys())

    drifts: list[PositionDrift] = []
    for sym in sorted(all_syms):
        book_pos = book_snap.position(sym)
        bq = book_pos.quantity if book_pos is not None else Decimal("0")
        eq = expected_qty.get(sym, Decimal("0"))
        drifts.append(PositionDrift(symbol=sym, book_quantity=bq, expected_quantity=eq))

    return ReconciliationReport(
        starting_cash=starting_cash,
        book_cash=book_snap.cash,
        expected_cash=expected_cash,
        position_drifts=tuple(drifts),
    )