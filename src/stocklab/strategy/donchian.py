"""Donchian channel breakout — the classic Turtle trend-following rule.

Enter when price closes above the highest high of the previous `entry` bars;
exit when it closes below the lowest low of the previous `exit` bars.

The windows deliberately **exclude the current bar**. Including it makes the
rule trivially self-satisfying — today's close is frequently the highest close
of a window that contains today — and that is a look-ahead bug wearing a
strategy's clothes.
"""

from __future__ import annotations

from .base import History, Weights


class DonchianBreakout:
    def __init__(self, symbol: str, entry: int = 55, exit: int = 20) -> None:
        if entry < 2 or exit < 2:
            raise ValueError("channel windows must be at least 2 bars")
        self.symbol = symbol
        self.entry = entry
        self.exit = exit
        self._long = False

    def on_bar(self, history: History) -> Weights:
        frame = history[self.symbol]
        if len(frame) < max(self.entry, self.exit) + 2:
            return {}

        highs = frame["high"].to_numpy(dtype=float, copy=False)
        lows = frame["low"].to_numpy(dtype=float, copy=False)
        close = frame["close"].to_numpy(dtype=float, copy=False)[-1]

        # `:-1` drops the current bar from both channels.
        entry_high = highs[-(self.entry + 1) : -1].max()
        exit_low = lows[-(self.exit + 1) : -1].min()

        if not self._long and close > entry_high:
            self._long = True
        elif self._long and close < exit_low:
            self._long = False

        return {self.symbol: 1.0 if self._long else 0.0}
