"""The choke point: the single, mandatory gate every order passes through.

This is the one place an :class:`OrderIntent` can turn into a real execution.
Nothing else in the system is allowed to talk to a broker directly, so the
invariant is structural, not merely a convention: *no order reaches a venue
without first surviving the gate chain, and every decision — approval, gate
rejection, broker rejection, fill — is written to the lineage chain.*

Flow
----
For each submitted intent, in order:

1. Record the **intent** to lineage (what was requested).
2. Run the **gate chain** against the *current* book and record the
   :class:`GateReport` (why it was allowed or blocked). Buying-power is checked
   against the book *before* any fill mutates it — the account must afford the
   order at decision time.
3. If the gates **reject**, record a terminal ``gate_rejected`` note and return
   without ever touching the broker.
4. If the gates **approve**, hand the intent to the :class:`Broker`, record the
   :class:`ExecutionReport`, and — only if it filled — apply the resulting
   :class:`Fill` to the book.

The choke point reads no clock and no randomness: the caller supplies the
``timestamp`` used for every lineage record of a submission, so a replay of the
same intents with the same timestamps reproduces an identical chain.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ledgerline.book.book import Book
from ledgerline.core.types import OrderIntent
from ledgerline.execution.paper_broker import Broker, ExecutionReport
from ledgerline.gates.gates import GateChain, GateReport
from ledgerline.lineage.lineage import LineageLog

__all__ = ["ChokeOutcome", "ChokeResult", "ChokePoint"]

class ChokeOutcome(str, Enum):
    """Terminal disposition of one intent at the choke point."""

    FILLED = "FILLED"          # gates approved, broker filled, book updated
    GATE_REJECTED = "GATE_REJECTED"    # blocked before ever reaching the broker
    BROKER_REJECTED = "BROKER_REJECTED"  # gates approved but the venue declined


@dataclass(frozen=True, slots=True)
class ChokeResult:
    """The full account of one intent's journey through the choke point.

    ``gate_report`` is always present. ``execution`` is present exactly when the
    gates approved and the broker was reached. ``lineage_ids`` are the ids of the
    records this submission appended, in order, so a caller can point straight at
    the audit trail for this one decision.
    """

    outcome: ChokeOutcome
    intent: OrderIntent
    gate_report: GateReport
    execution: ExecutionReport | None
    lineage_ids: tuple[str, ...]

    @property
    def approved(self) -> bool:
        """Did the order clear the gates? (Independent of the venue's answer.)"""
        return self.outcome is not ChokeOutcome.GATE_REJECTED

    @property
    def filled(self) -> bool:
        return self.outcome is ChokeOutcome.FILLED


class ChokePoint:
    """The single mandatory path from an approved intent to a real execution.

    Wires together the four collaborators — a :class:`GateChain` (risk and
    compliance), a :class:`Broker` (the venue), a :class:`Book` (the account it
    mutates on fills), and a :class:`LineageLog` (the tamper-evident audit
    trail). All are injected, so the same choke point drives a paper broker in a
    backtest or a live adapter in production with no code change.
    """

    def __init__(
        self,
        *,
        gate_chain: GateChain,
        broker: Broker,
        book: Book,
        lineage: LineageLog,
    ) -> None:
        self._gates = gate_chain
        self._broker = broker
        self._book = book
        self._lineage = lineage

    @property
    def book(self) -> Book:
        return self._book

    @property
    def lineage(self) -> LineageLog:
        return self._lineage

    def _intent_payload(self, intent: OrderIntent) -> dict[str, Any]:
        return {
            "symbol": intent.symbol,
            "side": intent.side.value,
            "quantity": str(intent.quantity),
            "limit_price": str(intent.limit_price),
        }

    def submit(self, intent: OrderIntent, *, timestamp: str) -> ChokeResult:
        """Run one intent through the full gate → broker → book pipeline.

        Every step is recorded to lineage under ``timestamp``. Returns a
        :class:`ChokeResult` describing where the order ended up and pointing at
        the lineage records it produced.
        """
        ids: list[str] = []

        # 1. Record what was requested, before any decision is made.
        ids.append(
            self._lineage.append(
                "order_intent", self._intent_payload(intent), timestamp=timestamp
            ).id
        )

        # 2. Run the gates against the CURRENT book (pre-fill) and record why.
        report = self._gates.evaluate(intent, self._book)
        ids.append(
            self._lineage.append(
                "gate_report", report.to_payload(), timestamp=timestamp
            ).id
        )

        # 3. Fail closed: a blocked order never reaches the broker.
        if not report.approved:
            ids.append(
                self._lineage.append(
                    "gate_rejected",
                    {
                        "symbol": intent.symbol,
                        "rejected_by": [r.gate for r in report.rejections],
                    },
                    timestamp=timestamp,
                ).id
            )
            return ChokeResult(
                outcome=ChokeOutcome.GATE_REJECTED,
                intent=intent,
                gate_report=report,
                execution=None,
                lineage_ids=tuple(ids),
            )

        # 4. Approved: hand to the venue and record its answer.
        execution = self._broker.submit(intent)
        ids.append(
            self._lineage.append(
                "execution_report", execution.to_payload(), timestamp=timestamp
            ).id
        )

        # 5. Only a real fill mutates the book.
        if execution.filled and execution.fill is not None:
            self._book.apply(execution.fill)
            outcome = ChokeOutcome.FILLED
        else:
            outcome = ChokeOutcome.BROKER_REJECTED

        return ChokeResult(
            outcome=outcome,
            intent=intent,
            gate_report=report,
            execution=execution,
            lineage_ids=tuple(ids),
        )