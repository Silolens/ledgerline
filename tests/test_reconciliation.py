"""Tests for reconciliation: book vs broker fill record."""

from decimal import Decimal

import pytest

from ledgerline.book.book import Book
from ledgerline.core.types import OrderIntent, Side
from ledgerline.execution.paper_broker import PaperBroker
from ledgerline.execution.choke_point import ChokePoint
from ledgerline.gates.gates import BuyingPowerGate, ComplianceGate, GateChain
from ledgerline.lineage.lineage import LineageLog
from ledgerline.reconcile.reconciliation import PositionDrift, ReconciliationReport, reconcile

TS = "2026-07-29T14:30:00Z"
START = Decimal("100000")


def build(*, cash="100000", reference_prices=None):
    book = Book(cash)
    lineage = LineageLog()
    chain = GateChain([ComplianceGate(), BuyingPowerGate()])
    broker = PaperBroker(reference_prices=reference_prices)
    cp = ChokePoint(gate_chain=chain, broker=broker, book=book, lineage=lineage)
    return cp, book, broker


# --- Clean state ------------------------------------------------------------

def test_clean_on_empty_fills():
    _, book, broker = build()
    report = reconcile(book, broker.fills, starting_cash=START)
    assert report.clean is True
    assert report.cash_drift == Decimal("0")
    assert report.position_drifts == ()


def test_clean_after_single_buy():
    cp, book, broker = build()
    cp.submit(OrderIntent("AAPL", Side.BUY, "10", "150"), timestamp=TS)
    report = reconcile(book, broker.fills, starting_cash=START)
    assert report.clean is True


def test_clean_after_multiple_fills():
    cp, book, broker = build()
    cp.submit(OrderIntent("AAPL", Side.BUY, "10", "150"), timestamp=TS)
    cp.submit(OrderIntent("MSFT", Side.BUY, "5", "300"), timestamp=TS)
    report = reconcile(book, broker.fills, starting_cash=START)
    assert report.clean is True


def test_clean_after_buy_then_partial_sell():
    cp, book, broker = build()
    cp.submit(OrderIntent("AAPL", Side.BUY, "10", "150"), timestamp=TS)
    cp.submit(OrderIntent("AAPL", Side.SELL, "4", "155"), timestamp=TS)
    report = reconcile(book, broker.fills, starting_cash=START)
    assert report.clean is True


# --- Drift detection --------------------------------------------------------

def test_cash_drift_detected_when_book_mutated_externally():
    cp, book, broker = build()
    cp.submit(OrderIntent("AAPL", Side.BUY, "10", "150"), timestamp=TS)
    # Simulate external mutation: apply an extra fill directly to the book
    # without going through the broker.
    from ledgerline.book.book import Fill
    book.apply(Fill("AAPL", Side.BUY, "5", "150"))
    report = reconcile(book, broker.fills, starting_cash=START)
    assert report.clean is False
    # Book paid an extra 750; cash is lower than expected.
    assert report.cash_drift == Decimal("-750")


def test_position_drift_detected_when_book_has_extra_position():
    _, book, broker = build()
    from ledgerline.book.book import Fill
    # Apply a fill to the book that the broker never recorded.
    book.apply(Fill("AAPL", Side.BUY, "10", "150"))
    report = reconcile(book, broker.fills, starting_cash=START)
    assert report.clean is False
    drift = next(d for d in report.position_drifts if d.symbol == "AAPL")
    assert drift.book_quantity == Decimal("10")
    assert drift.expected_quantity == Decimal("0")
    assert drift.quantity_drift == Decimal("10")


def test_position_drift_detected_when_broker_has_extra_fill():
    _, book, broker = build()
    # Broker records a fill that was never applied to the book.
    broker.submit(OrderIntent("AAPL", Side.BUY, "10", "150"))
    report = reconcile(book, broker.fills, starting_cash=START)
    assert report.clean is False
    drift = next(d for d in report.position_drifts if d.symbol == "AAPL")
    assert drift.book_quantity == Decimal("0")
    assert drift.expected_quantity == Decimal("10")
    assert drift.quantity_drift == Decimal("-10")


# --- Report structure -------------------------------------------------------

def test_report_is_frozen():
    _, book, broker = build()
    report = reconcile(book, broker.fills, starting_cash=START)
    assert isinstance(report, ReconciliationReport)
    with pytest.raises((AttributeError, Exception)):
        report.book_cash = Decimal("0")  # type: ignore[misc]


def test_position_drift_is_frozen():
    _, book, broker = build()
    from ledgerline.book.book import Fill
    book.apply(Fill("AAPL", Side.BUY, "5", "100"))
    report = reconcile(book, broker.fills, starting_cash=START)
    drift = report.position_drifts[0]
    assert isinstance(drift, PositionDrift)
    with pytest.raises((AttributeError, Exception)):
        drift.book_quantity = Decimal("0")  # type: ignore[misc]


def test_position_drifts_sorted_by_symbol():
    cp, book, broker = build()
    cp.submit(OrderIntent("MSFT", Side.BUY, "5", "300"), timestamp=TS)
    cp.submit(OrderIntent("AAPL", Side.BUY, "10", "150"), timestamp=TS)
    report = reconcile(book, broker.fills, starting_cash=START)
    symbols = [d.symbol for d in report.position_drifts]
    assert symbols == sorted(symbols)


def test_starting_cash_recorded_on_report():
    _, book, broker = build()
    report = reconcile(book, broker.fills, starting_cash=START)
    assert report.starting_cash == START


def test_expected_cash_computed_from_fills_not_book():
    cp, book, broker = build()
    cp.submit(OrderIntent("AAPL", Side.BUY, "10", "150"), timestamp=TS)
    report = reconcile(book, broker.fills, starting_cash=START)
    assert report.expected_cash == START - Decimal("1500")
    assert report.book_cash == report.expected_cash


# --- Reference-price fill (price improvement) --------------------------------

def test_clean_with_reference_price_fill():
    # Broker fills at reference (148), not limit (150); both book and expected
    # cash must reflect the actual fill price.
    cp, book, broker = build(reference_prices={"AAPL": "148"})
    cp.submit(OrderIntent("AAPL", Side.BUY, "10", "150"), timestamp=TS)
    report = reconcile(book, broker.fills, starting_cash=START)
    assert report.clean is True
    assert report.expected_cash == START - Decimal("1480")

def test_reconciliation_uses_contract_multiplier_for_cash():
    register_spec(
        InstrumentSpec("ESR", AssetClass.FUTURE, contract_multiplier=Decimal("50"))
    )
    book = Book(Decimal("100000"))
    fill = Fill("ESR", Side.BUY, Decimal("2"), Decimal("5000"))
    book.apply(fill)
    report = reconcile(book, [fill], starting_cash=Decimal("100000"))
    assert report.clean is True
    assert report.expected_cash == Decimal("100000") - Decimal("500000")
