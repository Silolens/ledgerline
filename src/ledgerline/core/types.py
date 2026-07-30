"""Canonical value types shared across the whole system.

Design rules enforced here:

* **Money is Decimal, never float.** Prices, quantities, cash and P&L all
  flow through :class:`decimal.Decimal`. Binary floats silently misrepresent
  values like ``0.1`` and accumulate rounding error across millions of fills;
  in an accounting system that is a correctness bug, not a cosmetic one.
* **Types are frozen / immutable.** An ``OrderIntent`` describes a *decision*.
  Once formed it is never mutated in place — downstream stages produce new
  records rather than editing the request, which keeps the audit lineage
  honest and makes replay deterministic.
* **No wall-clock or randomness in construction.** Anything that must be
  timestamped receives the time from the caller, so tests and replays are
  reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Union

__all__ = ["Side", "Money", "Quantity", "Price", "OrderIntent", "to_decimal"]


# A permissive input alias: we accept ints, strings and Decimals when building
# money-like values, but *never* bare floats (see ``to_decimal``).
Numeric = Union[int, str, Decimal]

# Semantic aliases. They are all Decimal at runtime but the names document
# intent at call sites (a Price is not a Quantity even if both are Decimal).
Money = Decimal
Quantity = Decimal
Price = Decimal


def to_decimal(value: Numeric, *, field: str = "value") -> Decimal:
    """Coerce ``value`` to :class:`~decimal.Decimal`, rejecting floats.

    Floats are refused deliberately: ``Decimal(0.1)`` yields
    ``0.1000000000000000055511151231257827021181583404541015625``. Callers
    must pass an int, a string, or an existing Decimal so the represented
    value is exactly what was intended.
    """
    if isinstance(value, bool):
        # bool is a subclass of int; almost always a bug in a money context.
        raise TypeError(f"{field} must not be a bool, got {value!r}")
    if isinstance(value, float):
        raise TypeError(
            f"{field} must not be a float ({value!r}); pass a str/int/Decimal "
            "to avoid binary rounding error"
        )
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, str)):
        return Decimal(value)
    raise TypeError(f"{field} has unsupported type {type(value).__name__}")


class Side(str, Enum):
    """Direction of an order. ``str`` mixin makes it JSON/serialization friendly."""

    BUY = "BUY"
    SELL = "SELL"

    @property
    def sign(self) -> int:
        """+1 for a buy, -1 for a sell — used to signed-update positions."""
        return 1 if self is Side.BUY else -1


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """An immutable request to trade — the input to the choke point.

    This is a *decision*, not yet an order at a broker. It carries the minimal
    fields the gates and execution layer need. Prices/quantities are coerced
    to Decimal on construction so no float can leak in through the front door.
    """

    symbol: str
    side: Side
    quantity: Quantity
    limit_price: Price

    def __post_init__(self) -> None:
        # dataclass is frozen, so we set coerced values via object.__setattr__.
        qty = to_decimal(self.quantity, field="quantity")
        px = to_decimal(self.limit_price, field="limit_price")
        if not self.symbol or not self.symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        if qty <= 0:
            raise ValueError(f"quantity must be positive, got {qty}")
        if px <= 0:
            raise ValueError(f"limit_price must be positive, got {px}")
        if not isinstance(self.side, Side):
            raise TypeError(f"side must be a Side, got {type(self.side).__name__}")
        object.__setattr__(self, "quantity", qty)
        object.__setattr__(self, "limit_price", px)
