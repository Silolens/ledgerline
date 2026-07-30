"""Tests for the PaperBroker: fill semantics, determinism, blotter, protocol.

The paper broker is the interchangeable stand-in for a live venue behind the
:class:`Broker` protocol. These tests pin its contract: marketable-limit fills
at the reference price, fill-at-limit when no market data is set, rejection of
non-marketable orders, deterministic counter-based ids, an ordered blotter, and
that a produced Fill applies cleanly to a Book.
"""

from decimal import Decimal

import pytest

from ledgerline.book.book import Book, Fill
from ledgerline.core.types import OrderIntent, Side
from ledgerline.execution.paper_broker import (
    Broker,
    ExecutionReport,
    OrderStatus,
    PaperBroker,
)


def intent(symbol="AAPL", side=Side.BUY, qty="100", price="150"):
    return OrderIntent(symbol, side, qty, price)


# --- Fill-at-limit fallback (no reference price) ----------------------------

def test_fills_at_limit_when_no_reference_price():
    broker = PaperBroker()
    report = broker.submit(intent("AAPL", Side.BUY, "10", "150"))
    assert report.status is OrderStatus.FILLED
    assert report.filled is True
    assert report.fill is not None
    assert report.fill.price == Decimal("150")
    assert report.fill.quantity == Decimal("10")
    assert report.fill.side is Side.BUY


def test_fill_applies_cleanly_to_a_book():
    broker = PaperBroker()
    book = Book("100000")
    report = broker.submit(intent("AAPL", Side.BUY, "10", "150"))
    book.apply(report.fill)
    pos = book.position("AAPL")
    assert pos is not None
    assert pos.quantity == Decimal("10")
    assert book.cash == Decimal("100000") - Decimal("1500")


# --- Marketable-limit BUY ---------------------------------------------------

def test_marketable_buy_fills_at_reference_price():
    # reference 148 <= limit 150 -> marketable; fills at 148 (price improvement).
    broker = PaperBroker(reference_prices={"AAPL": "148"})
    report = broker.submit(intent("AAPL", Side.BUY, "10", "150"))
    assert report.status is OrderStatus.FILLED
    assert report.fill.price == Decimal("148")


def test_non_marketable_buy_is_rejected():
    # reference 152 > limit 150 -> not marketable.
    broker = PaperBroker(reference_prices={"AAPL": "152"})
    report = broker.submit(intent("AAPL", Side.BUY, "10", "150"))
    assert report.status is OrderStatus.REJECTED
    assert report.filled is False
    assert report.fill is None
    assert "not marketable" in report.reason


def test_buy_at_reference_equal_to_limit_fills():
    # boundary: reference == limit is marketable.
    broker = PaperBroker(reference_prices={"AAPL": "150"})
    report = broker.submit(intent("AAPL", Side.BUY, "10", "150"))
    assert report.status is OrderStatus.FILLED
    assert report.fill.price == Decimal("150")


# --- Marketable-limit SELL (symmetry) ---------------------------------------

def test_marketable_sell_fills_at_reference_price():
    # reference 152 >= limit 150 -> marketable; fills at 152 (price improvement).
    broker = PaperBroker(reference_prices={"AAPL": "152"})
    report = broker.submit(intent("AAPL", Side.SELL, "10", "150"))
    assert report.status is OrderStatus.FILLED
    assert report.fill.price == Decimal("152")


def test_non_marketable_sell_is_rejected():
    # reference 148 < limit 150 -> not marketable for a sell.
    broker = PaperBroker(reference_prices={"AAPL": "148"})
    report = broker.submit(intent("AAPL", Side.SELL, "10", "150"))
    assert report.status is OrderStatus.REJECTED
    assert report.fill is None


def test_sell_at_reference_equal_to_limit_fills():
    broker = PaperBroker(reference_prices={"AAPL": "150"})
    report = broker.submit(intent("AAPL", Side.SELL, "10", "150"))
    assert report.status is OrderStatus.FILLED
    assert report.fill.price == Decimal("150")


# --- Deterministic order ids ------------------------------------------------

def test_order_ids_are_sequential_and_formatted():
    broker = PaperBroker()
    r1 = broker.submit(intent())
    r2 = broker.submit(intent())
    r3 = broker.submit(intent())
    assert r1.broker_order_id == "PAPER-000001"
    assert r2.broker_order_id == "PAPER-000002"
    assert r3.broker_order_id == "PAPER-000003"


def test_rejected_orders_still_consume_an_id():
    # An id is minted before marketability is checked, so ids stay monotonic
    # regardless of outcome — a rejection does not reuse the next fill's id.
    broker = PaperBroker(reference_prices={"AAPL": "152"})
    rejected = broker.submit(intent("AAPL", Side.BUY, "10", "150"))
    broker.set_price("AAPL", "148")
    filled = broker.submit(intent("AAPL", Side.BUY, "10", "150"))
    assert rejected.broker_order_id == "PAPER-000001"
    assert filled.broker_order_id == "PAPER-000002"


def test_custom_id_prefix():
    broker = PaperBroker(id_prefix="SIM")
    assert broker.submit(intent()).broker_order_id == "SIM-000001"


def test_ids_are_deterministic_across_replays():
    def build():
        broker = PaperBroker(reference_prices={"AAPL": "148"})
        return [broker.submit(intent("AAPL", Side.BUY, "10", "150")).broker_order_id
                for _ in range(3)]

    assert build() == build()


# --- Blotter and fills ------------------------------------------------------

def test_blotter_records_every_report_in_order():
    broker = PaperBroker(reference_prices={"AAPL": "152"})
    broker.submit(intent("AAPL", Side.BUY, "10", "150"))   # rejected
    broker.set_price("AAPL", "148")
    broker.submit(intent("AAPL", Side.BUY, "10", "150"))   # filled
    blotter = broker.blotter
    assert len(blotter) == 2
    assert blotter[0].status is OrderStatus.REJECTED
    assert blotter[1].status is OrderStatus.FILLED


def test_fills_yields_only_filled_reports():
    broker = PaperBroker(reference_prices={"AAPL": "152"})
    broker.submit(intent("AAPL", Side.BUY, "10", "150"))   # rejected -> no fill
    broker.set_price("AAPL", "148")
    broker.submit(intent("AAPL", Side.BUY, "10", "150"))   # filled
    fills = broker.fills
    assert len(fills) == 1
    assert isinstance(fills[0], Fill)
    assert fills[0].price == Decimal("148")


def test_blotter_is_an_immutable_snapshot():
    broker = PaperBroker()
    broker.submit(intent())
    snapshot = broker.blotter
    broker.submit(intent())
    # The earlier snapshot must not grow retroactively.
    assert len(snapshot) == 1
    assert len(broker.blotter) == 2


# --- Reference-price feed ---------------------------------------------------

def test_set_price_canonicalizes_symbol_to_upper():
    broker = PaperBroker()
    broker.set_price("aapl", "148")
    assert broker.reference_price("AAPL") == Decimal("148")
    assert broker.reference_price("aapl") == Decimal("148")


def test_reference_price_is_none_for_unknown_symbol():
    broker = PaperBroker()
    assert broker.reference_price("AAPL") is None


def test_set_price_rejects_non_positive():
    broker = PaperBroker()
    with pytest.raises(ValueError):
        broker.set_price("AAPL", "0")
    with pytest.raises(ValueError):
        broker.set_price("AAPL", "-1")


def test_set_price_rejects_float():
    broker = PaperBroker()
    with pytest.raises(TypeError):
        broker.set_price("AAPL", 148.5)


# --- ExecutionReport payload ------------------------------------------------

def test_report_payload_is_json_safe_for_a_fill():
    broker = PaperBroker(reference_prices={"AAPL": "148"})
    report = broker.submit(intent("AAPL", Side.BUY, "10", "150"))
    p = report.to_payload()
    assert p["broker_order_id"] == "PAPER-000001"
    assert p["status"] == "FILLED"
    assert p["symbol"] == "AAPL"
    assert p["side"] == "BUY"
    assert p["quantity"] == "10"
    assert p["limit_price"] == "150"
    assert p["fill_price"] == "148"


def test_report_payload_for_a_rejection_has_no_fill_price():
    broker = PaperBroker(reference_prices={"AAPL": "152"})
    report = broker.submit(intent("AAPL", Side.BUY, "10", "150"))
    p = report.to_payload()
    assert p["status"] == "REJECTED"
    assert p["fill_price"] is None


# --- Protocol conformance ---------------------------------------------------

def test_paper_broker_satisfies_the_broker_protocol():
    assert isinstance(PaperBroker(), Broker)


def test_execution_report_is_frozen():
    broker = PaperBroker()
    report = broker.submit(intent())
    with pytest.raises((AttributeError, Exception)):
        report.status = OrderStatus.REJECTED  # type: ignore[misc]