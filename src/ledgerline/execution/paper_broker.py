"""Paper broker: a deterministic, in-memory execution venue.

The choke point, once it has approved an :class:`OrderIntent`, hands it to a
*broker* for execution. In production that broker is a network adapter to a real
venue; for development, backtests and the vertical slice it is this in-memory
simulator. Both satisfy the same :class:`Broker` protocol, so the choke point
never knows which one it is talking to.

Fill model
----------
* **Marketable-limit.** If a reference price is known for the symbol, the order
  fills only when it is marketable — a BUY when ``reference <= limit``, a SELL
  when ``reference >= limit`` — and fills *at the reference price* (the market
  price, which is at least as good as the limit: price improvement). A
  non-marketable order comes back ``REJECTED`` rather than resting on a book;
  a real resting-order/queue model can come later behind the same protocol.
* **Fill-at-limit fallback.** If no reference price has been set for the symbol,
  the order fills at its own limit price. This keeps the slice runnable for any
  ticker before a market-data feed is wired in.

Determinism
-----------
Order ids come from a monotonic counter (``PAPER-000001``), never a clock or
randomness, so a replay of the same intents produces the same ids and fills.
Every execution — filled or rejected — is recorded on the :attr:`blotter`, the
broker's own independent record that reconciliation later checks the Book
against.

This broker does **not** run the gates. Risk and compliance are the choke
point's job; by the time an intent reaches here it has already been approved.
The broker's only job is to turn an approved intent into an execution report.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol, runtime_checkable

from ledgerline.book.book import Fill
from ledgerline.core.types import Numeric, OrderIntent, Price, Side, to_decimal

__all__ = [
    "OrderStatus",
    "ExecutionReport",
    "Broker",
    "PaperBroker",
]


class OrderStatus(str, Enum):
    """Terminal outcome of submitting an order to the broker."""

    FILLED = "FILLED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    """The broker's answer to one submitted order.

    ``fill`` is present exactly when ``status is FILLED`` and is the fact the
    Book applies. ``reason`` explains the outcome (useful for a rejection and
    for lineage). Frozen: an execution is a fact, not an editable request.
    """

    broker_order_id: str
    status: OrderStatus
    intent: OrderIntent
    fill: Fill | None
    reason: str

    @property
    def filled(self) -> bool:
        return self.status is OrderStatus.FILLED

    def to_payload(self) -> dict[str, Any]:
        """JSON-safe structure the choke point folds into a lineage record."""
        return {
            "broker_order_id": self.broker_order_id,
            "status": self.status.value,
            "reason": self.reason,
            "symbol": self.intent.symbol,
            "side": self.intent.side.value,
            "quantity": str(self.intent.quantity),
            "limit_price": str(self.intent.limit_price),
            "fill_price": (str(self.fill.price) if self.fill is not None else None),
        }


@runtime_checkable
class Broker(Protocol):
    """A venue that turns an approved :class:`OrderIntent` into an execution.

    The choke point depends only on this surface, so the paper simulator and a
    future live adapter are interchangeable.
    """

    def submit(self, intent: OrderIntent) -> ExecutionReport: ...


class PaperBroker:
    """In-memory, deterministic execution venue (see module docstring)."""

    def __init__(
        self,
        *,
        id_prefix: str = "PAPER",
        reference_prices: Mapping[str, Numeric] | None = None,
    ) -> None:
        self._id_prefix = id_prefix
        self._seq = 0
        self._prices: dict[str, Price] = {}
        self._blotter: list[ExecutionReport] = []
        for sym, px in (reference_prices or {}).items():
            self.set_price(sym, px)

    # --- reference-price feed ----------------------------------------------

    def set_price(self, symbol: str, price: Numeric) -> None:
        """Set the reference (market) price used to fill ``symbol``.

        Passing a price makes fills marketability-checked and filled at this
        price; with no price set the symbol fills at the order's own limit.
        """
        px = to_decimal(price, field="price")
        if px <= 0:
            raise ValueError(f"reference price must be positive, got {px}")
        self._prices[symbol.upper()] = px

    def reference_price(self, symbol: str) -> Price | None:
        return self._prices.get(symbol.upper())

    # --- blotter ------------------------------------------------------------

    @property
    def blotter(self) -> tuple[ExecutionReport, ...]:
        """Every execution report this broker has produced, in order."""
        return tuple(self._blotter)

    @property
    def fills(self) -> tuple[Fill, ...]:
        """The fills of every filled order — the broker's independent record."""
        return tuple(r.fill for r in self._blotter if r.fill is not None)

    # --- execution ----------------------------------------------------------

    def _next_order_id(self) -> str:
        self._seq += 1
        return f"{self._id_prefix}-{self._seq:06d}"

    def submit(self, intent: OrderIntent) -> ExecutionReport:
        """Execute an approved intent and record the report on the blotter."""
        order_id = self._next_order_id()
        ref = self._prices.get(intent.symbol.upper())

        if ref is None:
            # No market data: assume marketable, fill at the limit price.
            fill_price = intent.limit_price
        else:
            marketable = (
                ref <= intent.limit_price
                if intent.side is Side.BUY
                else ref >= intent.limit_price
            )
            if not marketable:
                report = ExecutionReport(
                    broker_order_id=order_id,
                    status=OrderStatus.REJECTED,
                    intent=intent,
                    fill=None,
                    reason=(
                        f"not marketable: {intent.side.value} limit "
                        f"{intent.limit_price} vs reference {ref}"
                    ),
                )
                self._blotter.append(report)
                return report
            # Marketable: fill at the market price (at least as good as limit).
            fill_price = ref

        fill = Fill(
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            price=fill_price,
        )
        report = ExecutionReport(
            broker_order_id=order_id,
            status=OrderStatus.FILLED,
            intent=intent,
            fill=fill,
            reason=f"filled {intent.quantity} @ {fill_price}",
        )
        self._blotter.append(report)
        return report