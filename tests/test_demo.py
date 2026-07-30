"""Tests for the end-to-end vertical slice (ledgerline.demo)."""

from decimal import Decimal

from ledgerline.core.types import Side
from ledgerline.demo import DemoResult, run_demo
from ledgerline.execution.choke_point import ChokeOutcome


def test_demo_runs_and_reconciles_clean():
    result = run_demo()
    assert isinstance(result, DemoResult)
    assert result.clean is True
    assert result.reconciliation.clean is True
    assert result.lineage_intact is True


def test_demo_produces_both_buy_and_sell():
    result = run_demo()
    sides = {r.intent.side for r in result.results}
    assert Side.BUY in sides
    assert Side.SELL in sides


def test_demo_every_order_filled():
    # With no reference prices the paper broker fills at the limit, so every
    # gate-approved intent should fill.
    result = run_demo()
    assert len(result.results) >= 2
    assert len(result.filled) == len(result.results)


def test_demo_cash_has_no_drift():
    result = run_demo()
    assert result.reconciliation.cash_drift == Decimal("0")


def test_demo_is_deterministic():
    # Same inputs -> identical cash and identical lineage integrity.
    a = run_demo()
    b = run_demo()
    assert a.reconciliation.book_cash == b.reconciliation.book_cash
    assert a.reconciliation.expected_cash == b.reconciliation.expected_cash
    assert [r.outcome for r in a.results] == [r.outcome for r in b.results]


def test_demo_no_signals_when_flat():
    # A flat price series never crosses, so no orders and a trivially clean book.
    result = run_demo(closes=("100",) * 8)
    assert result.results == ()
    assert result.clean is True


def test_demo_compliance_blocks_are_not_triggered_by_default():
    # Default symbol is not restricted; nothing should be gate-rejected.
    result = run_demo()
    assert all(r.approved for r in result.results)


# --- reference-price marketability path -------------------------------------

def test_demo_reference_price_gives_price_improvement():
    # A mark between the BUY limit (20) and SELL limit (10) makes both intents
    # marketable, and the broker fills AT the mark rather than the order's own
    # limit -- deterministic price improvement. The book still reconciles clean
    # because the book and the reconciliation both derive from the same fills.
    result = run_demo(reference_prices={"AAPL": "15"})
    assert result.clean is True
    filled = result.filled
    assert len(filled) >= 2
    for r in filled:
        assert r.execution is not None and r.execution.fill is not None
        assert r.execution.fill.price == Decimal("15")


def test_demo_non_marketable_intent_is_broker_rejected_and_clean():
    # closes rise but never fall, so the signal emits a single BUY (limit 20).
    # A mark above that limit is not marketable, so the broker rejects the order
    # without touching the book. That is a clean terminal outcome: no fill, no
    # position, no cash movement -- and the run still reconciles clean.
    result = run_demo(
        closes=("10", "10", "10", "20", "20"),
        reference_prices={"AAPL": "25"},
    )
    assert len(result.results) == 1
    r = result.results[0]
    assert r.intent.side is Side.BUY
    assert r.outcome is ChokeOutcome.BROKER_REJECTED
    assert r.filled is False
    assert result.filled == ()
    # Gate approved it (fail-closed only on gate rejection); the broker declined.
    assert r.approved is True
    # Book untouched -> no drift, chain intact.
    assert result.reconciliation.book_cash == Decimal("100000")
    assert result.reconciliation.cash_drift == Decimal("0")
    assert result.clean is True