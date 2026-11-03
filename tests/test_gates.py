"""Tests for the Gates: compliance deny-list, buying-power/margin, and the chain.

The gates are the last conservative line before execution: they must block what
the account can't afford or isn't allowed to trade, allow risk-reducing orders,
and fail closed on any error. These tests pin that behavior — including the
signed-position margin math that mirrors the Book's flip logic.
"""

from decimal import Decimal

import pytest

from ledgerline.book.book import Book, Fill
from ledgerline.core.instruments import AssetClass, InstrumentSpec, register_spec
from ledgerline.core.types import OrderIntent, Side
from ledgerline.gates.gates import (
    BuyingPowerGate,
    ComplianceGate,
    Gate,
    GateChain,
    GateDecision,
    GateReport,
    Verdict,
)


def intent(symbol="AAPL", side=Side.BUY, qty="100", price="150"):
    return OrderIntent(symbol, side, qty, price)


# --- ComplianceGate ---------------------------------------------------------

def test_compliance_passes_unrestricted_symbol():
    gate = ComplianceGate(blocked_symbols=["TSLA"])
    d = gate.evaluate(intent("AAPL"), Book("100000"))
    assert d.verdict is Verdict.PASS
    assert d.passed is True


def test_compliance_blocks_restricted_symbol():
    gate = ComplianceGate(blocked_symbols=["TSLA"])
    d = gate.evaluate(intent("TSLA"), Book("100000"))
    assert d.verdict is Verdict.REJECT
    assert d.passed is False
    assert "restricted" in d.reason


def test_compliance_deny_list_is_case_insensitive():
    gate = ComplianceGate(blocked_symbols=["tsla"])
    # OrderIntent canonicalizes to upper; the deny-list does too.
    assert gate.evaluate(intent("TSLA"), Book("100000")).verdict is Verdict.REJECT
    assert gate.blocked_symbols == frozenset({"TSLA"})


def test_compliance_empty_deny_list_passes_everything():
    gate = ComplianceGate()
    assert gate.evaluate(intent("ANYTHING"), Book("100000")).passed


# --- BuyingPowerGate: opening -----------------------------------------------

def test_buying_power_passes_when_affordable():
    gate = BuyingPowerGate()
    # need 100 * 150 = 15000, have 100000.
    d = gate.evaluate(intent("AAPL", Side.BUY, "100", "150"), Book("100000"))
    assert d.verdict is Verdict.PASS
    assert d.detail["required_margin"] == "15000"


def test_buying_power_rejects_when_unaffordable():
    gate = BuyingPowerGate()
    # need 100 * 150 = 15000, have 10000.
    d = gate.evaluate(intent("AAPL", Side.BUY, "100", "150"), Book("10000"))
    assert d.verdict is Verdict.REJECT
    assert "insufficient buying power" in d.reason


def test_buying_power_exact_cash_is_allowed():
    gate = BuyingPowerGate()
    # need exactly 15000, have exactly 15000 -> not greater than, so PASS.
    d = gate.evaluate(intent("AAPL", Side.BUY, "100", "150"), Book("15000"))
    assert d.verdict is Verdict.PASS


def test_buying_power_short_open_requires_margin():
    gate = BuyingPowerGate()
    # Opening a short is exposure-increasing: still needs margin.
    d = gate.evaluate(intent("AAPL", Side.SELL, "100", "150"), Book("10000"))
    assert d.verdict is Verdict.REJECT


# --- BuyingPowerGate: reducing / closing / flipping -------------------------

def test_reducing_a_long_needs_no_margin():
    gate = BuyingPowerGate()
    book = Book("0")  # no cash at all
    book.apply(Fill("AAPL", Side.BUY, "100", "150"))  # long 100
    # Selling 40 reduces exposure -> allowed even with zero cash.
    d = gate.evaluate(intent("AAPL", Side.SELL, "40", "150"), book)
    assert d.verdict is Verdict.PASS
    assert d.detail["exposure_increase_qty"] == "0"
    assert "reduces exposure" in d.reason


def test_full_close_needs_no_margin():
    gate = BuyingPowerGate()
    book = Book("0")
    book.apply(Fill("AAPL", Side.BUY, "100", "150"))
    d = gate.evaluate(intent("AAPL", Side.SELL, "100", "150"), book)
    assert d.verdict is Verdict.PASS


def test_covering_a_short_needs_no_margin():
    gate = BuyingPowerGate()
    book = Book("0")
    book.apply(Fill("AAPL", Side.SELL, "100", "150"))  # short 100
    d = gate.evaluate(intent("AAPL", Side.BUY, "50", "150"), book)
    assert d.verdict is Verdict.PASS


def test_flip_charges_margin_only_on_the_excess():
    gate = BuyingPowerGate()
    book = Book("3000")
    book.apply(Fill("AAPL", Side.BUY, "100", "150"))  # long 100, cash now 3000
    # Sell 150: closes 100 (free) + opens short 50 (needs 50*150=7500).
    d = gate.evaluate(intent("AAPL", Side.SELL, "150", "150"), book)
    assert d.detail["exposure_increase_qty"] == "50"
    assert d.detail["required_margin"] == "7500"
    assert d.verdict is Verdict.REJECT  # only 3000 cash


def test_flip_excess_affordable_passes():
    gate = BuyingPowerGate()
    book = Book("10000")
    book.apply(Fill("AAPL", Side.BUY, "100", "150"))
    # Excess 50 * 150 = 7500 <= 10000 -> PASS.
    d = gate.evaluate(intent("AAPL", Side.SELL, "150", "150"), book)
    assert d.verdict is Verdict.PASS


# --- BuyingPowerGate: leverage and multipliers ------------------------------

def test_margin_rate_models_leverage():
    gate = BuyingPowerGate(margin_rate="0.5")
    # need 0.5 * 100 * 150 = 7500, have 10000.
    d = gate.evaluate(intent("AAPL", Side.BUY, "100", "150"), Book("10000"))
    assert d.verdict is Verdict.PASS
    assert d.detail["required_margin"] == "7500.0"


def test_margin_rate_must_be_positive():
    with pytest.raises(ValueError):
        BuyingPowerGate(margin_rate="0")


def test_contract_multiplier_scales_required_margin():
    register_spec(
        InstrumentSpec("ES", AssetClass.FUTURE, contract_multiplier=Decimal("50"))
    )
    gate = BuyingPowerGate()
    # need 5000 * 2 * 50 = 500000, have 100000 -> REJECT.
    d = gate.evaluate(intent("ES", Side.BUY, "2", "5000"), Book("100000"))
    assert d.verdict is Verdict.REJECT
    assert d.detail["required_margin"] == "500000"


# --- GateDecision / GateReport payloads -------------------------------------

def test_decision_payload_is_json_safe():
    d = GateDecision("g", Verdict.PASS, "ok", {"n": "1"})
    p = d.to_payload()
    assert p == {"gate": "g", "verdict": "PASS", "reason": "ok", "detail": {"n": "1"}}


def test_report_approved_only_when_all_pass():
    ok = GateDecision("a", Verdict.PASS, "")
    bad = GateDecision("b", Verdict.REJECT, "nope")
    assert GateReport((ok, ok)).approved is True
    assert GateReport((ok, bad)).approved is False
    assert GateReport((ok, bad)).rejections == (bad,)


def test_report_payload_round_trips_decisions():
    ok = GateDecision("a", Verdict.PASS, "ok")
    bad = GateDecision("b", Verdict.REJECT, "no")
    payload = GateReport((ok, bad)).to_payload()
    assert payload["approved"] is False
    assert [d["gate"] for d in payload["decisions"]] == ["a", "b"]


# --- GateChain --------------------------------------------------------------

def test_chain_approves_when_all_gates_pass():
    chain = GateChain([ComplianceGate(["TSLA"]), BuyingPowerGate()])
    report = chain.evaluate(intent("AAPL", Side.BUY, "100", "150"), Book("100000"))
    assert report.approved is True
    assert len(report.decisions) == 2


def test_chain_rejects_if_any_gate_rejects():
    chain = GateChain([ComplianceGate(["AAPL"]), BuyingPowerGate()])
    report = chain.evaluate(intent("AAPL", Side.BUY, "100", "150"), Book("100000"))
    assert report.approved is False
    # Compliance rejects; buying power still evaluated (no short-circuit).
    assert len(report.decisions) == 2
    assert report.rejections[0].gate == "compliance"


def test_chain_collects_all_decisions_for_lineage():
    chain = GateChain([ComplianceGate(["AAPL"]), BuyingPowerGate()])
    report = chain.evaluate(intent("AAPL", Side.BUY, "100", "150"), Book("1"))
    # Both gates reject; both recorded.
    assert len(report.rejections) == 2
    assert {r.gate for r in report.rejections} == {"compliance", "buying_power"}


def test_chain_fails_closed_when_a_gate_raises():
    class Exploding:
        name = "boom"

        def evaluate(self, intent, book):
            raise RuntimeError("kaboom")

    chain = GateChain([Exploding(), BuyingPowerGate()])
    report = chain.evaluate(intent(), Book("100000"))
    assert report.approved is False
    boom = report.rejections[0]
    assert boom.gate == "boom"
    assert "RuntimeError" in boom.reason


def test_empty_chain_is_rejected_at_configuration_time():
    with pytest.raises(ValueError, match="at least one risk gate"):
        GateChain([])


def test_empty_gate_report_is_not_approved():
    assert GateReport(()).approved is False


def test_unknown_instrument_is_rejected_by_gate_chain():
    report = GateChain([BuyingPowerGate()]).evaluate(
        intent("UNKNOWN", Side.BUY, "1", "10"), Book("100000")
    )
    assert report.approved is False
    assert "KeyError" in report.rejections[0].reason


def test_order_price_must_match_instrument_tick():
    register_spec(InstrumentSpec("TICK", AssetClass.EQUITY, tick_size=Decimal("0.05")))
    report = GateChain([BuyingPowerGate()]).evaluate(
        intent("TICK", Side.BUY, "1", "10.03"), Book("100000")
    )
    assert report.approved is False
    assert "tick_size" in report.rejections[0].reason


def test_order_quantity_must_match_instrument_lot():
    register_spec(InstrumentSpec("LOT", AssetClass.EQUITY, lot_size=Decimal("5")))
    report = GateChain([BuyingPowerGate()]).evaluate(
        intent("LOT", Side.BUY, "3", "10"), Book("100000")
    )
    assert report.approved is False
    assert "lot_size" in report.rejections[0].reason


def test_instrument_increments_and_multiplier_must_be_positive():
    with pytest.raises(ValueError, match="tick_size must be positive"):
        InstrumentSpec("BADTICK", AssetClass.EQUITY, tick_size=Decimal("0"))
    with pytest.raises(ValueError, match="lot_size must be positive"):
        InstrumentSpec("BADLOT", AssetClass.EQUITY, lot_size=Decimal("-1"))


# --- Protocol conformance ---------------------------------------------------

def test_concrete_gates_satisfy_the_protocol():
    assert isinstance(ComplianceGate(), Gate)
    assert isinstance(BuyingPowerGate(), Gate)