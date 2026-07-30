"""Tests for the ChokePoint: the mandatory gate → broker → book → lineage path.

These pin the structural invariant: no order reaches the broker without clearing
the gates, gates are checked against the pre-fill book, only real fills mutate
the book, and every step (intent, gate report, rejection/execution) lands on the
lineage chain. The chain must verify after every submission.
"""

from decimal import Decimal

import pytest

from ledgerline.book.book import Book
from ledgerline.core.types import OrderIntent, Side
from ledgerline.execution.choke_point import ChokeOutcome, ChokePoint, ChokeResult
from ledgerline.execution.paper_broker import OrderStatus, PaperBroker
from ledgerline.gates.gates import BuyingPowerGate, ComplianceGate, GateChain
from ledgerline.lineage.lineage import LineageLog

TS = "2026-07-29T14:30:00Z"


def intent(symbol="AAPL", side=Side.BUY, qty="10", price="150"):
    return OrderIntent(symbol, side, qty, price)


def build(*, cash="100000", blocked=(), reference_prices=None, margin_rate="1"):
    book = Book(cash)
    lineage = LineageLog()
    chain = GateChain([ComplianceGate(blocked), BuyingPowerGate(margin_rate)])
    broker = PaperBroker(reference_prices=reference_prices)
    cp = ChokePoint(gate_chain=chain, broker=broker, book=book, lineage=lineage)
    return cp, book, lineage, broker


# --- Happy path -------------------------------------------------------------

def test_approved_order_fills_and_updates_the_book():
    cp, book, _, broker = build(reference_prices={"AAPL": "148"})
    result = cp.submit(intent("AAPL", Side.BUY, "10", "150"), timestamp=TS)
    assert result.outcome is ChokeOutcome.FILLED
    assert result.filled is True
    assert result.approved is True
    # Filled at the reference price (148), so the book paid 1480, not 1500.
    assert book.cash == Decimal("100000") - Decimal("1480")
    assert book.position("AAPL").quantity == Decimal("10")
    # Broker actually saw the order.
    assert len(broker.blotter) == 1


def test_approved_fill_at_limit_when_no_reference_price():
    cp, book, _, _ = build()
    result = cp.submit(intent("AAPL", Side.BUY, "10", "150"), timestamp=TS)
    assert result.filled is True
    assert book.cash == Decimal("100000") - Decimal("1500")


# --- Gate rejection short-circuits the broker -------------------------------

def test_compliance_rejection_never_reaches_the_broker():
    cp, book, _, broker = build(blocked=["AAPL"])
    result = cp.submit(intent("AAPL"), timestamp=TS)
    assert result.outcome is ChokeOutcome.GATE_REJECTED
    assert result.approved is False
    assert result.filled is False
    assert result.execution is None
    # The book is untouched and the broker never saw the order.
    assert book.cash == Decimal("100000")
    assert book.position("AAPL") is None
    assert len(broker.blotter) == 0


def test_buying_power_rejection_never_reaches_the_broker():
    cp, book, _, broker = build(cash="1000")  # can't afford 10 * 150 = 1500
    result = cp.submit(intent("AAPL", Side.BUY, "10", "150"), timestamp=TS)
    assert result.outcome is ChokeOutcome.GATE_REJECTED
    assert len(broker.blotter) == 0
    assert book.cash == Decimal("1000")


def test_gate_rejected_records_which_gates_blocked():
    cp, _, lineage, _ = build(cash="1", blocked=["AAPL"])
    cp.submit(intent("AAPL"), timestamp=TS)
    # intent, gate_report, gate_rejected == 3 records.
    kinds = [r.kind for r in lineage.records]
    assert kinds == ["order_intent", "gate_report", "gate_rejected"]
    rejected_by = lineage.records[-1].payload["rejected_by"]
    assert set(rejected_by) == {"compliance", "buying_power"}


# --- Gates are evaluated against the PRE-fill book --------------------------

def test_gates_see_the_book_before_the_fill_is_applied():
    # Cash exactly covers one order; a second identical order must be rejected
    # because the first one already spent the cash.
    cp, book, _, broker = build(cash="1500")
    r1 = cp.submit(intent("AAPL", Side.BUY, "10", "150"), timestamp=TS)
    r2 = cp.submit(intent("AAPL", Side.BUY, "10", "150"), timestamp=TS)
    assert r1.filled is True
    assert r2.outcome is ChokeOutcome.GATE_REJECTED
    assert book.position("AAPL").quantity == Decimal("10")  # only the first
    assert len(broker.blotter) == 1


# --- Broker rejection: approved by gates, declined by venue -----------------

def test_broker_rejection_leaves_the_book_untouched():
    # Gates approve (affordable), but the order isn't marketable at the venue.
    cp, book, _, broker = build(reference_prices={"AAPL": "152"})
    result = cp.submit(intent("AAPL", Side.BUY, "10", "150"), timestamp=TS)
    assert result.outcome is ChokeOutcome.BROKER_REJECTED
    assert result.approved is True          # gates said yes
    assert result.filled is False
    assert result.execution.status is OrderStatus.REJECTED
    # Broker saw it, but nothing was applied to the book.
    assert len(broker.blotter) == 1
    assert book.cash == Decimal("100000")
    assert book.position("AAPL") is None


# --- Lineage completeness and integrity -------------------------------------

def test_full_pipeline_lands_three_records_for_a_fill():
    cp, _, lineage, _ = build(reference_prices={"AAPL": "148"})
    result = cp.submit(intent(), timestamp=TS)
    kinds = [r.kind for r in lineage.records]
    assert kinds == ["order_intent", "gate_report", "execution_report"]
    # The result points at exactly the records it produced, in order.
    assert result.lineage_ids == tuple(r.id for r in lineage.records)


def test_lineage_verifies_after_each_submission():
    cp, _, lineage, _ = build(reference_prices={"AAPL": "148"})
    cp.submit(intent("AAPL", Side.BUY, "10", "150"), timestamp=TS)
    assert lineage.verify() is True
    cp.submit(intent("MSFT", Side.BUY, "5", "300"), timestamp=TS)
    assert lineage.verify() is True


def test_lineage_records_the_gate_report_payload():
    cp, _, lineage, _ = build(reference_prices={"AAPL": "148"})
    cp.submit(intent(), timestamp=TS)
    gate_rec = next(r for r in lineage.records if r.kind == "gate_report")
    assert gate_rec.payload["approved"] is True
    gates = [d["gate"] for d in gate_rec.payload["decisions"]]
    assert gates == ["compliance", "buying_power"]


def test_intent_record_captures_the_request():
    cp, _, lineage, _ = build()
    cp.submit(intent("AAPL", Side.SELL, "7", "200"), timestamp=TS)
    intent_rec = lineage.records[0]
    assert intent_rec.kind == "order_intent"
    assert intent_rec.payload == {
        "symbol": "AAPL",
        "side": "SELL",
        "quantity": "7",
        "limit_price": "200",
    }


# --- Determinism ------------------------------------------------------------

def test_replaying_the_same_intents_reproduces_the_lineage_chain():
    def run():
        cp, _, lineage, _ = build(reference_prices={"AAPL": "148"})
        cp.submit(intent("AAPL", Side.BUY, "10", "150"), timestamp=TS)
        cp.submit(intent("AAPL", Side.BUY, "10", "150"), timestamp=TS)
        return [r.id for r in lineage.records]

    assert run() == run()


# --- Result / accessor surface ----------------------------------------------

def test_result_is_frozen():
    cp, _, _, _ = build()
    result = cp.submit(intent(), timestamp=TS)
    assert isinstance(result, ChokeResult)
    with pytest.raises((AttributeError, Exception)):
        result.outcome = ChokeOutcome.GATE_REJECTED  # type: ignore[misc]


def test_choke_point_exposes_book_and_lineage():
    cp, book, lineage, _ = build()
    assert cp.book is book
    assert cp.lineage is lineage