"""Turn a :class:`DemoResult` into a JSON-safe dict for an HTTP client.

The engine's value types are ``Decimal``-carrying, frozen dataclasses and string
enums — faithful in Python, but not directly ``json.dumps``-able (Decimal is not
JSON-serializable). This module walks a completed run and emits a plain dict of
``str``/``int``/``bool``/``None``/``list``/``dict`` only, with every money and
quantity rendered as its exact decimal *string* (never a float — the same
no-binary-float rule the core enforces).

It is a pure read: it adds no state and derives nothing the engine did not
already compute. ``demo_to_dict(run_demo())`` is the whole contract the browser
sees.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ledgerline.demo import DemoResult
from ledgerline.execution.choke_point import ChokeResult
from ledgerline.reconcile.reconciliation import PositionDrift, ReconciliationReport

__all__ = ["demo_to_dict"]


def _num(value: Decimal) -> str:
    """Render a Decimal as its exact string. No float ever appears in output."""
    return str(value)


def _order(result: ChokeResult) -> dict[str, Any]:
    intent = result.intent
    execution = result.execution
    fill_price = (
        _num(execution.fill.price)
        if execution is not None and execution.fill is not None
        else None
    )
    return {
        "outcome": result.outcome.value,
        "approved": result.approved,
        "filled": result.filled,
        "symbol": intent.symbol,
        "side": intent.side.value,
        "quantity": _num(intent.quantity),
        "limit_price": _num(intent.limit_price),
        "fill_price": fill_price,
        "broker_order_id": (
            execution.broker_order_id if execution is not None else None
        ),
        "reason": execution.reason if execution is not None else None,
        "gate_report": result.gate_report.to_payload(),
        "lineage_ids": list(result.lineage_ids),
    }


def _position_drift(drift: PositionDrift) -> dict[str, Any]:
    return {
        "symbol": drift.symbol,
        "book_quantity": _num(drift.book_quantity),
        "expected_quantity": _num(drift.expected_quantity),
        "quantity_drift": _num(drift.quantity_drift),
        "clean": drift.clean,
    }


def _reconciliation(report: ReconciliationReport) -> dict[str, Any]:
    return {
        "starting_cash": _num(report.starting_cash),
        "book_cash": _num(report.book_cash),
        "expected_cash": _num(report.expected_cash),
        "cash_drift": _num(report.cash_drift),
        "clean": report.clean,
        "position_drifts": [_position_drift(d) for d in report.position_drifts],
    }


def demo_to_dict(result: DemoResult) -> dict[str, Any]:
    """Serialize a completed run into a JSON-safe dict.

    Top-level keys:
      * ``orders``          — one entry per intent routed through the choke point
      * ``reconciliation``  — cash figures, drift, and per-symbol position drift
      * ``lineage_intact``  — did the sha256 audit chain verify end-to-end
      * ``clean``           — reconciled AND lineage verified (the headline badge)
      * ``summary``         — small counts for at-a-glance display
    """
    orders = [_order(r) for r in result.results]
    return {
        "orders": orders,
        "reconciliation": _reconciliation(result.reconciliation),
        "lineage_intact": result.lineage_intact,
        "clean": result.clean,
        "summary": {
            "orders": len(result.results),
            "filled": len(result.filled),
        },
    }