"""The load-bearing tests.

If these ever fail, every backtest result in this repo is void. They assert the
engine's central promise: a strategy cannot see, or act on, the future.
"""

from __future__ import annotations

import pandas as pd
import pytest

from stocklab.backtest import engine

from conftest import make_bars


class SpyStrategy:
    """Records exactly what it was shown on each call."""

    def __init__(self) -> None:
        self.seen: list[pd.DatetimeIndex] = []

    def on_bar(self, history):
        self.seen.append(next(iter(history.values())).index)
        return {}


def test_strategy_only_ever_sees_history_up_to_now(bars):
    spy = SpyStrategy()
    engine.run(bars, spy)

    full_index = bars["AAA"].index

    # One decision per bar except the last, which has no next open to fill on.
    assert len(spy.seen) == len(full_index) - 1

    for call, seen_index in enumerate(spy.seen):
        assert len(seen_index) == call + 1
        assert seen_index[-1] == full_index[call]
        assert seen_index.max() < full_index[call + 1]


def test_orders_fill_on_the_bar_after_the_decision(bars):
    class AlwaysLong:
        def on_bar(self, history):
            return {"AAA": 1.0}

    result = engine.run(bars, AlwaysLong())
    index = bars["AAA"].index

    assert result.fills, "expected at least one fill"
    first = result.fills[0]

    # The decision was made on bar 0; the fill belongs to bar 1, at its open.
    assert first.ts == index[1]
    assert first.price == pytest.approx(
        float(bars["AAA"]["open"].iloc[1]) * 1.0005, rel=1e-9
    )


def test_a_strategy_cannot_front_run_a_jump():
    """The behavioural version: reacting to a jump cannot capture that jump."""
    bars = make_bars(n_bars=40)
    jump_bar = 20

    # A 50% overnight gap: it appears in bar 20's close and bar 21's open.
    df = bars["AAA"]
    for column in ("open", "high", "low", "close"):
        df.iloc[jump_bar:, df.columns.get_loc(column)] *= 1.5
    df.iloc[jump_bar, df.columns.get_loc("open")] /= 1.5  # the gap opens mid-bar
    df.iloc[jump_bar, df.columns.get_loc("low")] /= 1.5

    class ChaseTheJump:
        """Goes all-in the moment a >20% move appears in its history."""

        def on_bar(self, history):
            closes = history["AAA"]["close"]
            if len(closes) >= 2 and closes.iloc[-1] / closes.iloc[-2] > 1.2:
                return {"AAA": 1.0}
            return {}

    result = engine.run(bars, ChaseTheJump(), initial_cash=100_000.0)

    # It was flat while the jump happened: it only learned of the move at the
    # close of bar 20, and could not buy before bar 21's already-elevated open.
    assert result.equity.iloc[jump_bar] == pytest.approx(100_000.0)
    assert all(fill.ts > df.index[jump_bar] for fill in result.fills)


def test_costs_are_applied_and_reduce_returns(bars):
    class AlwaysLong:
        def on_bar(self, history):
            return {"AAA": 1.0}

    free = engine.run(bars, AlwaysLong(), fee_bps=0.0, slippage_bps=0.0)
    costed = engine.run(bars, AlwaysLong(), fee_bps=1.0, slippage_bps=5.0)

    assert costed.equity.iloc[-1] < free.equity.iloc[-1]
    assert costed.stats["total_fees"] > 0.0


def test_leverage_is_rejected(bars):
    class Overexposed:
        def on_bar(self, history):
            return {"AAA": 1.5}

    with pytest.raises(ValueError, match="no leverage"):
        engine.run(bars, Overexposed())


def test_buy_and_hold_tracks_the_underlying(bars):
    from stocklab.strategy.buy_and_hold import BuyAndHold

    result = engine.run(bars, BuyAndHold(), fee_bps=0.0, slippage_bps=0.0)

    close = bars["AAA"]["close"]
    underlying_return = close.iloc[-1] / close.iloc[1] - 1.0
    strategy_return = result.equity.iloc[-1] / result.equity.iloc[1] - 1.0

    # Entered at bar 1's open rather than bar 1's close, so allow a bar of drift.
    assert strategy_return == pytest.approx(underlying_return, abs=0.02)
