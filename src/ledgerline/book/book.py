"""The Book: positions, cash, and realized/unrealized P&L.

This is the accounting core of the system. It answers three questions at any
moment: *what do we hold* (positions), *how much cash is free* (cash), and
*how are we doing* (realized + unrealized P&L). Every fill flows through here.

Accounting model
----------------
* **Average-cost lots.** A position tracks a signed quantity and a single
  average cost. Adding in the same direction blends the average; reducing or
  closing crystallizes **realized** P&L against the average cost; flipping
  through zero closes the old side (realizing) and opens a new one at the
  fill price. This is simpler than FIFO tax-lot tracking and correct for
  mark-to-market performance measurement, which is what the trading system
  needs; a separate tax-lot ledger can come later without changing callers.
* **Decimal only.** Cash, prices, quantities and P&L are all Decimal — no
  float ever enters (fills coerce through :func:`to_decimal`).
* **Cash is signed by side.** A buy spends ``notional`` cash; a sell adds it.
  Contract multipliers are honored via :class:`InstrumentSpec.notional`, so
  futures/options settle correct cash without the book knowing the asset class.
* **Snapshots are immutable.** :meth:`Book.snapshot` returns a frozen view for
  reporting/reconciliation so callers can't mutate live state by accident.

Design rules from the core layer carry through: no wall-clock or randomness in
here, so replays are deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping

from ledgerline.core.instruments import get_spec
from ledgerline.core.types import Money, Numeric, Price, Quantity, Side, to_decimal

__all__ = ["Position", "Fill", "Book", "BookSnapshot", "PositionSnapshot"]


@dataclass(frozen=True, slots=True)
class Fill:
    """An executed trade — the input that mutates the book.

    A fill is a fact (it already happened at the broker), so it is immutable
    and its numeric fields are coerced to Decimal on construction.
    """

    symbol: str
    side: Side
    quantity: Quantity
    price: Price

    def __post_init__(self) -> None:
        qty = to_decimal(self.quantity, field="quantity")
        px = to_decimal(self.price, field="price")
        if not self.symbol or not self.symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        if qty <= 0:
            raise ValueError(f"fill quantity must be positive, got {qty}")
        if px <= 0:
            raise ValueError(f"fill price must be positive, got {px}")
        if not isinstance(self.side, Side):
            raise TypeError(f"side must be a Side, got {type(self.side).__name__}")
        object.__setattr__(self, "symbol", self.symbol.upper())
        object.__setattr__(self, "quantity", qty)
        object.__setattr__(self, "price", px)


@dataclass(slots=True)
class Position:
    """A live, mutable position in one instrument (average-cost).

    ``quantity`` is signed: positive = long, negative = short, zero = flat.
    ``avg_cost`` is the average entry price of the *current* open quantity
    (undefined/zero when flat). ``realized_pnl`` accumulates crystallized P&L
    across the position's life.
    """

    symbol: str
    quantity: Quantity = Decimal("0")
    avg_cost: Price = Decimal("0")
    realized_pnl: Money = Decimal("0")

    def unrealized_pnl(self, mark: Numeric) -> Money:
        """Mark-to-market P&L of the open quantity at ``mark`` price."""
        if self.quantity == 0:
            return Decimal("0")
        px = to_decimal(mark, field="mark")
        spec = get_spec(self.symbol)
        # (mark - avg_cost) * signed_qty * multiplier. Works for shorts:
        # a short has negative quantity, so a price drop yields positive P&L.
        return (px - self.avg_cost) * self.quantity * spec.contract_multiplier


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    """Immutable point-in-time view of one position."""

    symbol: str
    quantity: Quantity
    avg_cost: Price
    realized_pnl: Money


@dataclass(frozen=True, slots=True)
class BookSnapshot:
    """Immutable point-in-time view of the whole book."""

    cash: Money
    positions: tuple[PositionSnapshot, ...]
    realized_pnl: Money

    def position(self, symbol: str) -> PositionSnapshot | None:
        key = symbol.upper()
        for p in self.positions:
            if p.symbol == key:
                return p
        return None


class Book:
    """Mutable accounting book. Apply fills; read cash, positions and P&L."""

    def __init__(self, starting_cash: Numeric = Decimal("0")) -> None:
        self._cash: Money = to_decimal(starting_cash, field="starting_cash")
        self._positions: dict[str, Position] = {}

    # --- reads --------------------------------------------------------------

    @property
    def cash(self) -> Money:
        return self._cash

    @property
    def realized_pnl(self) -> Money:
        return sum((p.realized_pnl for p in self._positions.values()), Decimal("0"))

    def position(self, symbol: str) -> Position | None:
        return self._positions.get(symbol.upper())

    def unrealized_pnl(self, marks: Mapping[str, Numeric]) -> Money:
        """Total unrealized P&L given a ``{symbol: mark_price}`` mapping.

        Symbols missing from ``marks`` contribute nothing (treated as unmarked),
        so a partial mark map is allowed for incremental reporting.
        """
        total = Decimal("0")
        for sym, pos in self._positions.items():
            if pos.quantity == 0:
                continue
            mark = marks.get(sym)
            if mark is None:
                continue
            total += pos.unrealized_pnl(mark)
        return total

    def equity(self, marks: Mapping[str, Numeric]) -> Money:
        """Cash + mark-to-market value of open positions.

        Equity = cash + unrealized. Realized P&L is already reflected in cash
        (fills moved cash at execution), so it must NOT be added again here.
        """
        return self._cash + self.unrealized_pnl(marks)

    def snapshot(self) -> BookSnapshot:
        positions = tuple(
            PositionSnapshot(p.symbol, p.quantity, p.avg_cost, p.realized_pnl)
            for p in self._positions.values()
        )
        return BookSnapshot(
            cash=self._cash,
            positions=positions,
            realized_pnl=self.realized_pnl,
        )

    # --- writes -------------------------------------------------------------

    def apply(self, fill: Fill) -> None:
        """Apply an executed fill: move cash and update the position/P&L."""
        spec = get_spec(fill.symbol)
        notional = spec.notional(fill.price, fill.quantity)

        # Cash: a buy spends notional, a sell receives it.
        self._cash -= notional * fill.side.sign

        pos = self._positions.get(fill.symbol)
        if pos is None:
            pos = Position(symbol=fill.symbol)
            self._positions[fill.symbol] = pos

        self._apply_to_position(pos, fill, spec.contract_multiplier)

    @staticmethod
    def _apply_to_position(
        pos: Position, fill: Fill, multiplier: Decimal
    ) -> None:
        signed_qty = fill.quantity * fill.side.sign
        old_qty = pos.quantity

        if old_qty == 0 or (old_qty > 0) == (signed_qty > 0):
            # Opening or adding in the same direction: blend average cost.
            new_qty = old_qty + signed_qty
            total_cost = pos.avg_cost * abs(old_qty) + fill.price * abs(signed_qty)
            pos.avg_cost = total_cost / abs(new_qty)
            pos.quantity = new_qty
            return

        # Opposite direction: reducing, closing, or flipping.
        closing_qty = min(abs(signed_qty), abs(old_qty))
        # Realized P&L on the closed portion, in the direction of the OLD side.
        old_sign = 1 if old_qty > 0 else -1
        pnl = (fill.price - pos.avg_cost) * closing_qty * old_sign * multiplier
        pos.realized_pnl += pnl

        new_qty = old_qty + signed_qty
        if new_qty == 0:
            # Fully flat.
            pos.quantity = Decimal("0")
            pos.avg_cost = Decimal("0")
        elif (new_qty > 0) == (old_qty > 0):
            # Reduced but same side: average cost unchanged, quantity shrinks.
            pos.quantity = new_qty
        else:
            # Flipped through zero: new position opens at the fill price.
            pos.quantity = new_qty
            pos.avg_cost = fill.price
