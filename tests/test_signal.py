"""Tests for Bar, synthetic_bars, and MACrossSignal."""

from decimal import Decimal

import pytest

from ledgerline.core.types import Side
from ledgerline.signal.bars import Bar, synthetic_bars
from ledgerline.signal.ma_cross import MACrossSignal, SignalEvent


# --- Bar --------------------------------------------------------------------

def test_bar_canonicalizes_symbol():
    b = Bar("aapl", "t0", "1", "2", "0.5", "1.5", "100")
    assert b.symbol == "AAPL"


def test_bar_coerces_fields_to_decimal():
    b = Bar("AAPL", "t0", "1", "2", "0.5", "1.5", "100")
    assert isinstance(b.close, Decimal)


def test_bar_is_frozen():
    b = Bar("AAPL", "t0", "1", "2", "0.5", "1.5", "100")
    with pytest.raises((AttributeError, Exception)):
        b.close = Decimal("99")  # type: ignore[misc]


# --- synthetic_bars ---------------------------------------------------------

def test_synthetic_bars_length():
    bars = synthetic_bars("AAPL", ["100", "101", "102"])
    assert len(bars) == 3


def test_synthetic_bars_ohlc_equal():
    bars = synthetic_bars("AAPL", ["150"])
    b = bars[0]
    assert b.open == b.high == b.low == b.close == Decimal("150")


def test_synthetic_bars_custom_timestamps():
    ts = ["2026-01-01", "2026-01-02"]
    bars = synthetic_bars("MSFT", ["300", "301"], timestamps=ts)
    assert bars[0].timestamp == "2026-01-01"
    assert bars[1].timestamp == "2026-01-02"


def test_synthetic_bars_default_timestamps_are_indices():
    bars = synthetic_bars("AAPL", ["1", "2", "3"])
    assert bars[0].timestamp == "0"
    assert bars[2].timestamp == "2"


# --- MACrossSignal construction ---------------------------------------------

def test_ma_cross_rejects_fast_ge_slow():
    with pytest.raises(ValueError):
        MACrossSignal(fast=5, slow=5)
    with pytest.raises(ValueError):
        MACrossSignal(fast=10, slow=3)


def test_ma_cross_rejects_non_positive_windows():
    with pytest.raises(ValueError):
        MACrossSignal(fast=0, slow=5)


# --- No signal before enough bars -------------------------------------------

def test_no_signal_before_slow_window_filled():
    sig = MACrossSignal(fast=2, slow=3)
    bars = synthetic_bars("AAPL", ["100", "101"])  # only 2 bars, need 3
    assert sig.update(bars) == []


# --- BUY crossover ----------------------------------------------------------

def test_buy_signal_on_upward_crossover():
    # fast=2, slow=3; craft a sequence where fast crosses above slow
    # prices: 10, 10, 10, 20, 20 -> fast MA rises above slow MA
    sig = MACrossSignal(fast=2, slow=3)
    closes = ["10", "10", "10", "20", "20"]
    bars = synthetic_bars("AAPL", closes)
    events = sig.update(bars)
    buys = [e for e in events if e.side is Side.BUY]
    assert len(buys) >= 1


# --- SELL crossover ---------------------------------------------------------

def test_sell_signal_on_downward_crossover():
    sig = MACrossSignal(fast=2, slow=3)
    # fast MA rises above slow MA first (bars 0-3), then drops below (bar 4)
    closes = ["10", "10", "20", "20", "10"]
    bars = synthetic_bars("AAPL", closes)
    events = sig.update(bars)
    sells = [e for e in events if e.side is Side.SELL]
    assert len(sells) >= 1


# --- Signal event structure -------------------------------------------------

def test_signal_event_has_ma_values():
    sig = MACrossSignal(fast=2, slow=3)
    closes = ["10", "10", "10", "20", "20"]
    bars = synthetic_bars("AAPL", closes)
    events = sig.update(bars)
    assert len(events) > 0
    e = events[0]
    assert isinstance(e.fast_ma, Decimal)
    assert isinstance(e.slow_ma, Decimal)
    assert isinstance(e, SignalEvent)


def test_signal_event_is_frozen():
    sig = MACrossSignal(fast=2, slow=3)
    closes = ["10", "10", "10", "20", "20"]
    bars = synthetic_bars("AAPL", closes)
    events = sig.update(bars)
    assert len(events) > 0
    with pytest.raises((AttributeError, Exception)):
        events[0].side = Side.SELL  # type: ignore[misc]


# --- Multi-symbol independence ----------------------------------------------

def test_signals_tracked_independently_per_symbol():
    sig = MACrossSignal(fast=2, slow=3)
    aapl = synthetic_bars("AAPL", ["10", "10", "10", "20", "20"])
    msft = synthetic_bars("MSFT", ["50", "50", "50", "50", "50"])
    # Interleave bars
    interleaved = [a for pair in zip(aapl, msft) for a in pair]
    events = sig.update(interleaved)
    # AAPL should produce a signal; MSFT (flat) should not
    aapl_events = [e for e in events if e.symbol == "AAPL"]
    msft_events = [e for e in events if e.symbol == "MSFT"]
    assert len(aapl_events) >= 1
    assert len(msft_events) == 0


# --- No duplicate signals on flat MA ----------------------------------------

def test_no_signal_when_mas_stay_equal():
    sig = MACrossSignal(fast=2, slow=3)
    # All same price -> fast MA == slow MA always, no crossover
    bars = synthetic_bars("AAPL", ["100"] * 10)
    events = sig.update(bars)
    assert events == []