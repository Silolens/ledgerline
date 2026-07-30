"""Moving-average crossover signal generator."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from ledgerline.core.types import Side
from ledgerline.signal.bars import Bar

__all__ = ["MACrossSignal", "SignalEvent"]


@dataclass(frozen=True, slots=True)
class SignalEvent:
    """A directional signal produced by the MA crossover."""

    symbol: str
    timestamp: str
    side: Side
    fast_ma: Decimal
    slow_ma: Decimal


class MACrossSignal:
    """Emits BUY when fast MA crosses above slow MA, SELL when it crosses below.

    Parameters
    ----------
    fast:
        Window length for the fast (short) moving average.
    slow:
        Window length for the slow (long) moving average. Must be > fast.
    """

    def __init__(self, *, fast: int, slow: int) -> None:
        if fast <= 0 or slow <= 0:
            raise ValueError("window lengths must be positive")
        if fast >= slow:
            raise ValueError("fast window must be shorter than slow window")
        self._fast = fast
        self._slow = slow

    @staticmethod
    def _sma(closes: Sequence[Decimal], window: int) -> Decimal:
        return sum(closes[-window:], Decimal("0")) / Decimal(window)

    def update(self, bars: Sequence[Bar]) -> list[SignalEvent]:
        """Scan *bars* in order and return every crossover event found.

        Requires at least ``slow + 1`` bars to detect the first crossover
        (one bar of history before the window is full). Bars for different
        symbols may be interleaved; each symbol is tracked independently.
        """
        closes_by_sym: dict[str, list[Decimal]] = {}
        prev_above: dict[str, bool | None] = {}
        events: list[SignalEvent] = []

        for bar in bars:
            sym = bar.symbol
            buf = closes_by_sym.setdefault(sym, [])
            buf.append(bar.close)

            if len(buf) < self._slow:
                prev_above.setdefault(sym, None)
                continue

            fast_ma = self._sma(buf, self._fast)
            slow_ma = self._sma(buf, self._slow)
            above = fast_ma > slow_ma

            prev = prev_above.get(sym)
            if prev is not None and above != prev:
                events.append(
                    SignalEvent(
                        symbol=sym,
                        timestamp=bar.timestamp,
                        side=Side.BUY if above else Side.SELL,
                        fast_ma=fast_ma,
                        slow_ma=slow_ma,
                    )
                )
            prev_above[sym] = above

        return events