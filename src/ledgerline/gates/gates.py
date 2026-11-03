"""Gates: the pre-trade checks every order must survive before execution.

A gate answers one yes/no question about a proposed :class:`OrderIntent` given
the current :class:`Book`, and returns a :class:`GateDecision` explaining itself.
The :class:`GateChain` runs an ordered set of gates and aggregates their verdicts
into a :class:`GateReport`. The choke point runs the chain and logs the report to
Lineage, so *why* an order was allowed or blocked is always on the record.

Design
------
* **Fail closed.** The chain approves an order only if *every* gate passes. Any
  reject — or any exception raised while evaluating — blocks the order. A gate
  that cannot decide must not silently allow.
* **Explainable.** Every decision carries a human-readable ``reason`` and a
  machine-readable ``detail`` mapping, so the report is useful in a post-mortem
  and serializable straight into the lineage payload.
* **Asset-class agnostic.** Sizing math goes through :func:`get_spec` and
  ``InstrumentSpec.notional``/``contract_multiplier``, never a hard-coded 1x, so
  futures and options size correctly with no gate changes.
* **Deterministic.** No wall-clock, no randomness; a gate is a pure function of
  (intent, book), so replays reproduce identical reports.

This module decides *whether* to trade, not *how much* to trade optimally —
position sizing / Kelly / vol-targeting live in the portfolio layer. Gates are
the last, conservative line: they stop orders the account cannot afford or is
forbidden to place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable, Mapping, Protocol, runtime_checkable

from ledgerline.book.book import Book
from ledgerline.core.instruments import get_spec
from ledgerline.core.types import Numeric, OrderIntent, to_decimal

__all__ = [
    "Verdict",
    "GateDecision",
    "GateReport",
    "Gate",
    "ComplianceGate",
    "BuyingPowerGate",
    "GateChain",
]


class Verdict(str, Enum):
    """The outcome of a single gate evaluation."""

    PASS = "PASS"
    REJECT = "REJECT"


@dataclass(frozen=True, slots=True)
class GateDecision:
    """One gate's verdict on one order, with its reasoning.

    ``gate`` is the gate's name, ``reason`` a short human-readable explanation,
    and ``detail`` a JSON-safe mapping of the numbers behind the decision (for
    lineage and debugging). Frozen so a decision cannot be rewritten after it is
    made.
    """

    gate: str
    verdict: Verdict
    reason: str
    detail: Mapping[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.verdict is Verdict.PASS

    def to_payload(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "detail": dict(self.detail),
        }


@dataclass(frozen=True, slots=True)
class GateReport:
    """The aggregated outcome of running a chain of gates on one order.

    ``approved`` is true only if every gate passed. ``rejections`` lists the
    decisions that blocked the order (empty when approved). ``to_payload`` yields
    a JSON-safe structure the choke point folds straight into a lineage record.
    """

    decisions: tuple[GateDecision, ...]

    @property
    def approved(self) -> bool:
        return bool(self.decisions) and all(d.passed for d in self.decisions)

    @property
    def rejections(self) -> tuple[GateDecision, ...]:
        return tuple(d for d in self.decisions if not d.passed)

    def to_payload(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "decisions": [d.to_payload() for d in self.decisions],
        }


@runtime_checkable
class Gate(Protocol):
    """A single pre-trade check.

    ``name`` identifies the gate in decisions and lineage. ``evaluate`` inspects
    the proposed order against the current book and returns its verdict; it must
    be a pure function of its inputs (no side effects, no clock, no randomness).
    """

    name: str

    def evaluate(self, intent: OrderIntent, book: Book) -> GateDecision: ...


# --- Concrete gates ---------------------------------------------------------


class ComplianceGate:
    """Blocks orders in restricted symbols (a static deny-list).

    The real compliance surface (restricted lists, wash-trade and self-match
    prevention, position-limit rules, jurisdictional bans) is far larger; this
    is the minimal, deterministic seam every one of those rules will plug into.
    Symbols are canonicalized upper-case so the check matches the rest of the
    system regardless of how the caller cased the input.
    """

    name = "compliance"

    def __init__(self, blocked_symbols: Iterable[str] = ()) -> None:
        self._blocked: frozenset[str] = frozenset(
            s.upper() for s in blocked_symbols
        )

    @property
    def blocked_symbols(self) -> frozenset[str]:
        return self._blocked

    def evaluate(self, intent: OrderIntent, book: Book) -> GateDecision:
        symbol = intent.symbol.upper()
        if symbol in self._blocked:
            return GateDecision(
                gate=self.name,
                verdict=Verdict.REJECT,
                reason=f"{symbol} is on the restricted list",
                detail={"symbol": symbol},
            )
        return GateDecision(
            gate=self.name,
            verdict=Verdict.PASS,
            reason=f"{symbol} is not restricted",
            detail={"symbol": symbol},
        )


class BuyingPowerGate:
    """Rejects orders the account cannot afford to open.

    Margin model
    ------------
    Only the *exposure-increasing* part of an order consumes buying power. An
    order that reduces or closes an existing position frees risk, not consumes
    it, so it needs no margin. An order that flips through zero needs margin only
    on the excess that opens the new side — mirroring exactly how the Book splits
    a fill into a closing portion and an opening portion.

    Required margin = ``limit_price × exposure_increase_qty × contract_multiplier
    × margin_rate``, checked against ``book.cash``. ``margin_rate`` defaults to
    ``1`` (fully cash-secured; buying power == cash). A lower rate models
    leverage (e.g. ``0.5`` = 2× Reg-T-style initial margin) without any other
    part of the system needing to know.
    """

    name = "buying_power"

    def __init__(self, margin_rate: Numeric = Decimal("1")) -> None:
        rate = to_decimal(margin_rate, field="margin_rate")
        if rate <= 0:
            raise ValueError(f"margin_rate must be positive, got {rate}")
        self._margin_rate: Decimal = rate

    @property
    def margin_rate(self) -> Decimal:
        return self._margin_rate

    @staticmethod
    def _split_order(
        current_qty: Decimal, order_signed_qty: Decimal
    ) -> tuple[Decimal, Decimal]:
        """Split an order into (closing_qty, exposure_increase_qty), both >= 0.

        Same direction as the current position (or opening from flat): the whole
        order increases exposure and nothing closes. Opposite direction: the
        order first closes up to the current size (freeing risk), and only any
        excess beyond that opens new exposure. This mirrors how the Book splits a
        fill into a closing portion and an opening portion.
        """
        order_abs = abs(order_signed_qty)
        if current_qty == 0 or (current_qty > 0) == (order_signed_qty > 0):
            return Decimal("0"), order_abs
        closing = min(order_abs, abs(current_qty))
        excess = order_abs - abs(current_qty)
        return closing, (excess if excess > 0 else Decimal("0"))

    def evaluate(self, intent: OrderIntent, book: Book) -> GateDecision:
        spec = get_spec(intent.symbol, strict=True)
        if intent.limit_price % spec.tick_size != 0:
            raise ValueError(
                f"limit_price {intent.limit_price} is not a multiple of "
                f"tick_size {spec.tick_size} for {spec.symbol}"
            )
        if intent.quantity % spec.lot_size != 0:
            raise ValueError(
                f"quantity {intent.quantity} is not a multiple of "
                f"lot_size {spec.lot_size} for {spec.symbol}"
            )
        mult = spec.contract_multiplier
        order_signed_qty = intent.quantity * intent.side.sign

        pos = book.position(intent.symbol)
        current_qty = pos.quantity if pos is not None else Decimal("0")

        closing_qty, increase_qty = self._split_order(current_qty, order_signed_qty)

        # Margin is charged only on the exposure-increasing portion.
        required = intent.limit_price * increase_qty * mult * self._margin_rate

        # The closing portion moves cash the same way a fill would: a sell
        # (sign -1) receives cash, a buy (sign +1) pays it. Crediting that here
        # lets a flip fund its opening excess from the proceeds of the close,
        # instead of being measured against stale post-open cash.
        cash_from_close = -intent.side.sign * closing_qty * intent.limit_price * mult
        available = book.cash + cash_from_close

        detail = {
            "symbol": intent.symbol.upper(),
            "current_qty": str(current_qty),
            "order_signed_qty": str(order_signed_qty),
            "closing_qty": str(closing_qty),
            "exposure_increase_qty": str(increase_qty),
            "required_margin": str(required),
            "available_cash": str(available),
            "margin_rate": str(self._margin_rate),
        }

        # Only exposure-increasing orders can be blocked; a purely
        # risk-reducing order (increase_qty == 0) always passes.
        if increase_qty > 0 and required > available:
            return GateDecision(
                gate=self.name,
                verdict=Verdict.REJECT,
                reason=(
                    f"insufficient buying power: need {required}, "
                    f"have {available}"
                ),
                detail=detail,
            )
        return GateDecision(
            gate=self.name,
            verdict=Verdict.PASS,
            reason=(
                f"buying power ok: need {required}, have {available}"
                if increase_qty > 0
                else "order reduces exposure; no margin required"
            ),
            detail=detail,
        )


# --- Chain ------------------------------------------------------------------


class GateChain:
    """Runs an ordered set of gates and aggregates their verdicts.

    Fail-closed on every axis: an order is approved only if every gate passes,
    and a gate that *raises* is converted into a REJECT decision rather than
    being allowed to crash the choke point or — worse — let the order through.
    The full list of decisions is preserved (the chain does not short-circuit)
    so lineage captures why *each* gate voted, not just the first blocker.
    """

    def __init__(self, gates: Iterable[Gate]) -> None:
        self._gates: tuple[Gate, ...] = tuple(gates)
        if not self._gates:
            raise ValueError("GateChain requires at least one risk gate")

    @property
    def gates(self) -> tuple[Gate, ...]:
        return self._gates

    def evaluate(self, intent: OrderIntent, book: Book) -> GateReport:
        decisions: list[GateDecision] = []
        for gate in self._gates:
            try:
                decisions.append(gate.evaluate(intent, book))
            except Exception as exc:  # fail closed: an erroring gate blocks.
                decisions.append(
                    GateDecision(
                        gate=getattr(gate, "name", gate.__class__.__name__),
                        verdict=Verdict.REJECT,
                        reason=f"gate raised {type(exc).__name__}: {exc}",
                        detail={"error": type(exc).__name__},
                    )
                )
        return GateReport(decisions=tuple(decisions))