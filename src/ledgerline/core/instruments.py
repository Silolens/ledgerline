"""Instrument specifications and the ``get_spec`` registry.

An :class:`InstrumentSpec` captures the contract-level facts the rest of the
system needs to reason about an instrument *without knowing its asset class*:
how to turn a (price, quantity) pair into notional cash, the minimum price
increment, and the lot size. Equities are ``contract_multiplier = 1`` and one
price unit == one currency unit; futures/options/FX will later supply their own
multipliers and tick schemes through the *same* interface, so every downstream
component (book, gates, margin, reconciliation) can stay asset-class agnostic.

``get_spec(symbol)`` is the single lookup seam. Today it serves a small static
equity registry; later it will front a data-backed reference-data service, but
callers never change.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from ledgerline.core.types import Numeric, Price, Quantity, to_decimal

__all__ = ["AssetClass", "InstrumentSpec", "get_spec", "register_spec"]


class AssetClass(str, Enum):
    EQUITY = "EQUITY"
    FUTURE = "FUTURE"
    OPTION = "OPTION"
    FX = "FX"
    CRYPTO = "CRYPTO"


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    """Immutable contract-level description of a tradable instrument."""

    symbol: str
    asset_class: AssetClass
    #: Currency units of notional per 1.0 of price per 1 unit of quantity.
    #: Equities = 1; an ES future would be 50; an equity option 100.
    contract_multiplier: Decimal = Decimal("1")
    #: Smallest legal price increment.
    tick_size: Decimal = Decimal("0.01")
    #: Smallest tradable quantity increment.
    lot_size: Decimal = Decimal("1")
    quote_currency: str = "USD"

    def __post_init__(self) -> None:
        # Symbols are canonicalized to upper-case at the boundary so every spec
        # in the system carries the same form the registry keys on, regardless
        # of how it was constructed or registered.
        if not self.symbol or not self.symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        object.__setattr__(self, "symbol", self.symbol.upper())
        for name, value in (
            ("contract_multiplier", self.contract_multiplier),
            ("tick_size", self.tick_size),
            ("lot_size", self.lot_size),
        ):
            amount = to_decimal(value, field=name)
            if amount <= 0:
                raise ValueError(f"{name} must be positive, got {amount}")
            object.__setattr__(self, name, amount)

    def notional(self, price: Numeric, quantity: Numeric) -> Decimal:
        """Cash value of ``quantity`` units at ``price``.

        ``price * quantity * contract_multiplier``. All inputs are coerced to
        Decimal (floats rejected) so the result is exact.
        """
        px: Price = to_decimal(price, field="price")
        qty: Quantity = to_decimal(quantity, field="quantity")
        return px * qty * self.contract_multiplier


# --- Registry ---------------------------------------------------------------

_REGISTRY: dict[str, InstrumentSpec] = {}


def register_spec(spec: InstrumentSpec) -> None:
    """Add or replace a spec in the registry (keyed by upper-cased symbol)."""
    _REGISTRY[spec.symbol.upper()] = spec


def get_spec(symbol: str, *, strict: bool = False) -> InstrumentSpec:
    """Return the :class:`InstrumentSpec` for ``symbol``.

    Unknown symbols retain the historical equity fallback unless strict=True.
    Risk gates must use strict lookup so missing reference data cannot authorize
    an order. Other prototype/reporting callers may opt into the fallback.
    """
    key = symbol.upper()
    spec = _REGISTRY.get(key)
    if spec is not None:
        return spec
    if strict:
        raise KeyError(f"no instrument specification registered for {key}")
    return InstrumentSpec(symbol=key, asset_class=AssetClass.EQUITY)


# A couple of seed equities so the registry is not empty in the demo.
for _sym in ("AAPL", "MSFT", "SPY"):
    register_spec(InstrumentSpec(symbol=_sym, asset_class=AssetClass.EQUITY))
