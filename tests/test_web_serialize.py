"""Tests for ``ledgerline.web.serialize.demo_to_dict``.

The serializer's whole job is to turn a completed run into a dict that
``json.dumps`` accepts with no custom encoder, while preserving the engine's
exact figures as decimal *strings* (never floats). These tests pin both:
JSON-safety and faithfulness to the underlying ``DemoResult``.
"""

from __future__ import annotations

import json
from decimal import Decimal

from ledgerline.demo import run_demo
from ledgerline.web.serialize import demo_to_dict

# Types the serialized output is allowed to contain. Notably: no Decimal, no
# float, no Enum, no dataclass — only what json.dumps handles natively.
_JSON_SCALARS = (str, int, bool, type(None))


def _assert_json_safe(value: object, path: str = "$") -> None:
    """Recursively assert every node is a JSON-native, non-float value."""
    if isinstance(value, dict):
        for key, sub in value.items():
            assert isinstance(key, str), f"{path}: non-str key {key!r}"
            _assert_json_safe(sub, f"{path}.{key}")
    elif isinstance(value, list):
        for i, sub in enumerate(value):
            _assert_json_safe(sub, f"{path}[{i}]")
    else:
        assert not isinstance(value, float), f"{path}: float leaked in ({value!r})"
        assert isinstance(value, _JSON_SCALARS), f"{path}: bad type {type(value)!r}"


def test_output_is_json_safe_and_round_trips() -> None:
    payload = demo_to_dict(run_demo())
    _assert_json_safe(payload)
    # json.dumps with no default= proves it: a Decimal/Enum here would raise.
    dumped = json.dumps(payload)
    assert json.loads(dumped) == payload


def test_top_level_shape() -> None:
    payload = demo_to_dict(run_demo())
    assert set(payload) == {
        "orders",
        "reconciliation",
        "lineage_intact",
        "clean",
        "summary",
    }
    assert isinstance(payload["orders"], list)
    assert payload["summary"] == {
        "orders": len(payload["orders"]),
        "filled": sum(1 for o in payload["orders"] if o["filled"]),
    }


def test_default_run_is_clean_and_intact() -> None:
    result = run_demo()
    payload = demo_to_dict(result)
    assert payload["clean"] is result.clean is True
    assert payload["lineage_intact"] is result.lineage_intact is True
    assert payload["reconciliation"]["clean"] is True
    assert payload["reconciliation"]["cash_drift"] == "0"


def test_order_fields_match_engine() -> None:
    result = run_demo()
    payload = demo_to_dict(result)
    assert len(payload["orders"]) == len(result.results)
    for order, res in zip(payload["orders"], result.results):
        assert order["outcome"] == res.outcome.value
        assert order["approved"] is res.approved
        assert order["filled"] is res.filled
        assert order["symbol"] == res.intent.symbol
        assert order["side"] == res.intent.side.value
        assert order["quantity"] == str(res.intent.quantity)
        assert order["limit_price"] == str(res.intent.limit_price)
        assert order["lineage_ids"] == list(res.lineage_ids)
        # gate_report is reused from the engine's own to_payload()
        assert order["gate_report"] == res.gate_report.to_payload()


def test_money_is_decimal_string_not_float() -> None:
    payload = demo_to_dict(run_demo())
    recon = payload["reconciliation"]
    for key in ("starting_cash", "book_cash", "expected_cash", "cash_drift"):
        assert isinstance(recon[key], str)
        # a string that parses cleanly as Decimal — the exact figure, no float
        Decimal(recon[key])
    for order in payload["orders"]:
        assert isinstance(order["quantity"], str)
        assert isinstance(order["limit_price"], str)
        if order["fill_price"] is not None:
            assert isinstance(order["fill_price"], str)
            Decimal(order["fill_price"])


def test_no_orders_when_signal_never_crosses() -> None:
    # Flat closes never trigger a crossover, so no intents are routed.
    payload = demo_to_dict(run_demo(closes=("100",) * 8))
    assert payload["orders"] == []
    assert payload["summary"] == {"orders": 0, "filled": 0}
    assert payload["clean"] is True


def test_price_improvement_fills_at_reference_mark() -> None:
    # With a reference mark, a marketable order fills *at the mark* — never worse
    # than its limit. "Better" is side-aware: a BUY fills at or below its limit,
    # a SELL at or above it.
    result = run_demo(reference_prices={"AAPL": "15"})
    payload = demo_to_dict(result)
    filled = [o for o in payload["orders"] if o["filled"]]
    assert filled, "expected at least one fill under price improvement"
    for order in filled:
        assert order["fill_price"] is not None
        fill = Decimal(order["fill_price"])
        limit = Decimal(order["limit_price"])
        assert fill == Decimal("15"), "marketable orders fill at the reference mark"
        if order["side"] == "BUY":
            assert fill <= limit
        else:  # SELL
            assert fill >= limit


def test_broker_rejection_still_reconciles_clean() -> None:
    # A high reference mark makes the SELL non-marketable -> broker rejects it,
    # the book is untouched, and reconciliation stays clean.
    result = run_demo(
        closes=("10", "10", "10", "20", "20"),
        reference_prices={"AAPL": "25"},
    )
    payload = demo_to_dict(result)
    outcomes = [o["outcome"] for o in payload["orders"]]
    assert "BROKER_REJECTED" in outcomes
    rejected = next(o for o in payload["orders"] if o["outcome"] == "BROKER_REJECTED")
    assert rejected["filled"] is False
    assert rejected["fill_price"] is None
    assert payload["clean"] is True
    assert payload["reconciliation"]["clean"] is True