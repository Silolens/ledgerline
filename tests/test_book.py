"""Tests for the Book: positions, cash, and realized/unrealized P&L."""

from decimal import Decimal

import pytest

from ledgerline.book.book import Book, Fill, Position
from ledgerline.core.instruments import AssetClass, InstrumentSpec, register_spec
from ledgerline.core.types import Side


# --- Fill validation --------------------------------------------------------

def test_fill_coerces_and_canonicalizes():
    f = Fill("aapl", Side.BUY, "10", "150.25")
    assert f.symbol == "AAPL"
    assert f.quantity == Decimal("10")
    assert f.price == Decimal("150.25")


def test_fill_rejects_float_price():
    with pytest.raises(TypeError):
        Fill("AAPL", Side.BUY, "10", 150.25)


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(symbol="", side=Side.BUY, quantity="1", price="1"),
        dict(symbol="AAPL", side=Side.BUY, quantity="0", price="1"),
        dict(symbol="AAPL", side=Side.BUY, quantity="-1", price="1"),
        dict(symbol="AAPL", side=Side.BUY, quantity="1", price="0"),
    ],
)
def test_fill_validation(kwargs):
    with pytest.raises((ValueError, TypeError)):
        Fill(**kwargs)


# --- Opening and cash -------------------------------------------------------

def test_buy_opens_position_and_spends_cash():
    b = Book("100000")
    b.apply(Fill("AAPL", Side.BUY, "100", "150"))
    pos = b.position("AAPL")
    assert pos.quantity == Decimal("100")
    assert pos.avg_cost == Decimal("150")
    assert b.cash == Decimal("85000")  # 100000 - 100*150
    assert pos.realized_pnl == Decimal("0")


def test_sell_short_opens_and_receives_cash():
    b = Book("100000")
    b.apply(Fill("AAPL", Side.SELL, "100", "150"))
    pos = b.position("AAPL")
    assert pos.quantity == Decimal("-100")
    assert pos.avg_cost == Decimal("150")
    assert b.cash == Decimal("115000")  # 100000 + 100*150


# --- Averaging in -----------------------------------------------------------

def test_adding_same_side_blends_average_cost():
    b = Book("1000000")
    b.apply(Fill("AAPL", Side.BUY, "100", "100"))
    b.apply(Fill("AAPL", Side.BUY, "100", "200"))
    pos = b.position("AAPL")
    assert pos.quantity == Decimal("200")
    assert pos.avg_cost == Decimal("150")  # (100*100 + 100*200)/200
    assert pos.realized_pnl == Decimal("0")


# --- Reducing / closing / realized P&L --------------------------------------

def test_partial_close_realizes_pnl():
    b = Book("1000000")
    b.apply(Fill("AAPL", Side.BUY, "100", "100"))
    b.apply(Fill("AAPL", Side.SELL, "40", "150"))
    pos = b.position("AAPL")
    assert pos.quantity == Decimal("60")
    assert pos.avg_cost == Decimal("100")  # unchanged on reduce
    assert pos.realized_pnl == Decimal("2000")  # (150-100)*40


def test_full_close_flattens_and_realizes():
    b = Book("1000000")
    b.apply(Fill("AAPL", Side.BUY, "100", "100"))
    b.apply(Fill("AAPL", Side.SELL, "100", "120"))
    pos = b.position("AAPL")
    assert pos.quantity == Decimal("0")
    assert pos.avg_cost == Decimal("0")
    assert pos.realized_pnl == Decimal("2000")  # (120-100)*100


def test_short_then_cover_realizes_correctly():
    b = Book("1000000")
    b.apply(Fill("AAPL", Side.SELL, "100", "150"))  # short at 150
    b.apply(Fill("AAPL", Side.BUY, "100", "120"))   # cover at 120
    pos = b.position("AAPL")
    assert pos.quantity == Decimal("0")
    # short profits when price falls: (120-150)*100*(-1) = 3000
    assert pos.realized_pnl == Decimal("3000")


def test_flip_through_zero_opens_new_side():
    b = Book("1000000")
    b.apply(Fill("AAPL", Side.BUY, "100", "100"))   # long 100 @ 100
    b.apply(Fill("AAPL", Side.SELL, "150", "120"))  # sell 150 -> short 50
    pos = b.position("AAPL")
    assert pos.quantity == Decimal("-50")
    assert pos.avg_cost == Decimal("120")  # new short opened at fill price
    # only the 100 closed portion realizes: (120-100)*100 = 2000
    assert pos.realized_pnl == Decimal("2000")


# --- Unrealized / equity ----------------------------------------------------

def test_unrealized_pnl_long_and_short():
    b = Book("1000000")
    b.apply(Fill("AAPL", Side.BUY, "100", "100"))
    b.apply(Fill("MSFT", Side.SELL, "50", "300"))
    marks = {"AAPL": "110", "MSFT": "290"}
    # AAPL: (110-100)*100 = 1000 ; MSFT short: (290-300)*(-50) = 500
    assert b.unrealized_pnl(marks) == Decimal("1500")


def test_equity_is_cash_plus_unrealized_not_double_counting_realized():
    b = Book("100000")
    b.apply(Fill("AAPL", Side.BUY, "100", "100"))   # cash -> 90000
    b.apply(Fill("AAPL", Side.SELL, "50", "120"))   # cash += 6000 -> 96000, realized 1000
    assert b.cash == Decimal("96000")
    assert b.realized_pnl == Decimal("1000")
    # 50 shares left @ avg 100, mark 120 -> unrealized 1000
    eq = b.equity({"AAPL": "120"})
    assert eq == Decimal("97000")  # 96000 cash + 1000 unrealized


# --- Contract multiplier (futures) ------------------------------------------

def test_futures_multiplier_affects_cash_and_pnl():
    register_spec(
        InstrumentSpec("ES", AssetClass.FUTURE, contract_multiplier=Decimal("50"))
    )
    b = Book("1000000")
    b.apply(Fill("ES", Side.BUY, "2", "5000"))
    # notional = 5000 * 2 * 50 = 500000
    assert b.cash == Decimal("500000")
    b.apply(Fill("ES", Side.SELL, "2", "5010"))
    pos = b.position("ES")
    assert pos.quantity == Decimal("0")
    # realized = (5010-5000)*2*50 = 1000
    assert pos.realized_pnl == Decimal("1000")


# --- Snapshot immutability --------------------------------------------------

def test_snapshot_is_immutable_view():
    b = Book("100000")
    b.apply(Fill("AAPL", Side.BUY, "100", "150"))
    snap = b.snapshot()
    assert snap.cash == Decimal("85000")
    assert snap.position("AAPL").quantity == Decimal("100")
    with pytest.raises(Exception):
        snap.cash = Decimal("0")  # type: ignore[misc]


def test_unrealized_ignores_unmarked_symbols():
    b = Book("1000000")
    b.apply(Fill("AAPL", Side.BUY, "100", "100"))
    b.apply(Fill("MSFT", Side.BUY, "100", "200"))
    # only mark AAPL; MSFT contributes nothing
    assert b.unrealized_pnl({"AAPL": "110"}) == Decimal("1000")


def test_position_unrealized_flat_is_zero():
    p = Position(symbol="AAPL")
    assert p.unrealized_pnl("100") == Decimal("0")