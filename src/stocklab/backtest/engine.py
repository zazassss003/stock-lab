"""The backtest engine — the test suite for every idea in this repo.

Two properties matter more than anything else here:

1. **Look-ahead is structurally impossible.** The strategy is handed
   ``df.iloc[:i+1]`` — a slice that physically cannot contain a future bar —
   and the resulting orders fill at bar ``i+1``'s open. A strategy cannot act
   on a price it could not have known, because it was never given one.

2. **Costs always apply.** Fees and slippage are parameters with non-zero
   defaults. A backtest reporting zero costs is a bug, not a good result.

Keep both properties intact. Nearly every "too good to be true" backtest in
existence is one of these two rules quietly broken.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import numpy as np
import pandas as pd

from ..data.source import validate_bars
from ..strategy.base import Strategy

TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class Fill:
    ts: pd.Timestamp
    symbol: str
    qty: float  # positive = bought, negative = sold, in shares
    price: float  # execution price, slippage included
    fee: float


@dataclass
class BacktestResult:
    equity: pd.Series
    fills: list[Fill] = field(default_factory=list)

    @property
    def stats(self) -> dict[str, float]:
        returns = self.equity.pct_change().dropna()
        years = len(self.equity) / TRADING_DAYS_PER_YEAR
        total = self.equity.iloc[-1] / self.equity.iloc[0] - 1.0

        drawdown = self.equity / self.equity.cummax() - 1.0
        sharpe = (
            float(returns.mean() / returns.std() * np.sqrt(TRADING_DAYS_PER_YEAR))
            if returns.std() > 0
            else 0.0
        )

        return {
            "total_return": float(total),
            "cagr": float((1.0 + total) ** (1.0 / years) - 1.0) if years > 0 else 0.0,
            "max_drawdown": float(drawdown.min()),
            "sharpe": sharpe,
            "n_fills": float(len(self.fills)),
            "total_fees": float(sum(f.fee for f in self.fills)),
        }


def run(
    bars: Mapping[str, pd.DataFrame],
    strategy: Strategy,
    initial_cash: float = 100_000.0,
    fee_bps: float = 1.0,
    slippage_bps: float = 5.0,
    min_trade_notional: float = 1.0,
) -> BacktestResult:
    """Replay `bars` through `strategy`, one bar at a time.

    `fee_bps` and `slippage_bps` are in basis points (1 bp = 0.01%). The
    defaults are deliberately non-zero; pass 0 only to isolate a bug, never to
    report a result.
    """
    validate_bars(dict(bars))
    if fee_bps < 0 or slippage_bps < 0:
        raise ValueError("costs cannot be negative")

    index = next(iter(bars.values())).index
    fee_rate = fee_bps / 10_000.0
    slip_rate = slippage_bps / 10_000.0

    cash = float(initial_cash)
    positions: dict[str, float] = {symbol: 0.0 for symbol in bars}
    fills: list[Fill] = []
    equity_points: list[float] = []

    for i, ts in enumerate(index):
        closes = {symbol: float(df["close"].iloc[i]) for symbol, df in bars.items()}
        equity_points.append(cash + sum(positions[s] * closes[s] for s in bars))

        # The final bar has no next open to fill against, so nothing to decide.
        if i + 1 >= len(index):
            continue

        # The only view the strategy ever gets: bars 0..i inclusive.
        history = {symbol: df.iloc[: i + 1] for symbol, df in bars.items()}
        targets = _validate_targets(strategy.on_bar(history), bars)

        fill_ts = index[i + 1]
        opens = {symbol: float(df["open"].iloc[i + 1]) for symbol, df in bars.items()}
        equity_at_fill = cash + sum(positions[s] * opens[s] for s in bars)

        for symbol in bars:
            weight = targets.get(symbol, 0.0)
            reference_price = opens[symbol]
            target_shares = weight * equity_at_fill / reference_price
            delta = target_shares - positions[symbol]

            if abs(delta * reference_price) < min_trade_notional:
                continue

            # Slippage always works against us: pay up to buy, sell into the bid.
            direction = 1.0 if delta > 0 else -1.0
            price = reference_price * (1.0 + direction * slip_rate)
            notional = delta * price
            fee = abs(notional) * fee_rate

            cash -= notional + fee
            positions[symbol] += delta
            fills.append(Fill(fill_ts, symbol, delta, price, fee))

    return BacktestResult(
        equity=pd.Series(equity_points, index=index, name="equity"),
        fills=fills,
    )


def _validate_targets(
    targets: Mapping[str, float],
    bars: Mapping[str, pd.DataFrame],
) -> Mapping[str, float]:
    unknown = set(targets) - set(bars)
    if unknown:
        raise ValueError(f"strategy targeted unknown symbols: {sorted(unknown)}")

    gross = sum(abs(w) for w in targets.values())
    if gross > 1.0 + 1e-9:
        raise ValueError(f"gross exposure {gross:.3f} exceeds 1.0 — no leverage")

    return targets
