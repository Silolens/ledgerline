"""OHLCV bar type and synthetic bar construction."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from ledgerline.core.types import Numeric, to_decimal

__all__ = ["Bar", "synthetic_bars"]


@dataclass(frozen=True, slots=True)
class Bar:
    """One OHLCV bar for a symbol."""

    symbol: str
    timestamp: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", self.symbol.upper())
        for f in ("open", "high", "low", "close", "volume"):
            object.__setattr__(self, f, to_decimal(getattr(self, f), field=f))


def synthetic_bars(
    symbol: str,
    closes: Sequence[Numeric],
    *,
    timestamps: Sequence[str] | None = None,
) -> tuple[Bar, ...]:
    """Build tick bars (open=high=low=close) from a close-price sequence."""
    bars = []
    for i, px in enumerate(closes):
        ts = timestamps[i] if timestamps is not None else str(i)
        p = to_decimal(px, field="close")
        bars.append(
            Bar(symbol=symbol, timestamp=ts, open=p, high=p, low=p, close=p, volume=Decimal("0"))
        )
    return tuple(bars)