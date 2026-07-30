"""Tests for canonical types and instrument specs."""

from decimal import Decimal

import pytest

from ledgerline.core.instruments import (
    AssetClass,
    InstrumentSpec,
    get_spec,
    register_spec,
)
from ledgerline.core.types import OrderIntent, Side, to_decimal


# --- to_decimal -------------------------------------------------------------

def test_to_decimal_accepts_str_int_decimal():
    assert to_decimal("1.23") == Decimal("1.23")
    assert to_decimal(5) == Decimal("5")
    assert to_decimal(Decimal("2.5")) == Decimal("2.5")


def test_to_decimal_rejects_float():
    with pytest.raises(TypeError):
        to_decimal(0.1)


def test_to_decimal_rejects_bool():
    with pytest.raises(TypeError):
        to_decimal(True)


# --- Side -------------------------------------------------------------------

def test_side_sign():
    assert Side.BUY.sign == 1
    assert Side.SELL.sign == -1


# --- OrderIntent ------------------------------------------------------------

def test_order_intent_coerces_to_decimal():
    oi = OrderIntent("AAPL", Side.BUY, quantity="10", limit_price="150.25")
    assert oi.quantity == Decimal("10")
    assert oi.limit_price == Decimal("150.25")
    assert isinstance(oi.quantity, Decimal)


def test_order_intent_is_frozen():
    oi = OrderIntent("AAPL", Side.BUY, "10", "150")
    with pytest.raises(Exception):
        oi.quantity = Decimal("20")  # type: ignore[misc]


def test_order_intent_rejects_float_price():
    with pytest.raises(TypeError):
        OrderIntent("AAPL", Side.BUY, "10", 150.25)


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(symbol="", side=Side.BUY, quantity="1", limit_price="1"),
        dict(symbol="AAPL", side=Side.BUY, quantity="0", limit_price="1"),
        dict(symbol="AAPL", side=Side.BUY, quantity="-1", limit_price="1"),
        dict(symbol="AAPL", side=Side.BUY, quantity="1", limit_price="0"),
    ],
)
def test_order_intent_validation(kwargs):
    with pytest.raises((ValueError, TypeError)):
        OrderIntent(**kwargs)


# --- InstrumentSpec / registry ---------------------------------------------

def test_notional_equity():
    spec = get_spec("AAPL")
    assert spec.asset_class is AssetClass.EQUITY
    assert spec.notional("150.00", "10") == Decimal("1500.00")


def test_notional_uses_contract_multiplier():
    es = InstrumentSpec("ES", AssetClass.FUTURE, contract_multiplier=Decimal("50"))
    assert es.notional("5000", "2") == Decimal("500000")


def test_notional_rejects_float():
    with pytest.raises(TypeError):
        get_spec("AAPL").notional(150.0, 10)


def test_get_spec_unknown_defaults_to_equity():
    spec = get_spec("ZZZZ")
    assert spec.asset_class is AssetClass.EQUITY
    assert spec.contract_multiplier == Decimal("1")


def test_register_and_lookup_is_case_insensitive():
    register_spec(InstrumentSpec("tsla", AssetClass.EQUITY))
    assert get_spec("TSLA").symbol == "TSLA"
