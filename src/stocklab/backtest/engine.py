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

Modelling assumptions, stated so they are arguable rather than hidden:

- **Fractional shares.** Target weights convert to fractional share counts.
  True at Alpaca for liquid US equities; false at many brokers.
- **Unlimited liquidity at the next open**, plus fixed slippage. Fine for
  large-cap ETFs, optimistic for anything thin.
- **Cash earns `risk_free_annual`**, default 0. Leaving it at 0 penalises
  strategies that sit in cash, so it is conservative rather than flattering —
  but for an honest comparison against a real alternative, set it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import numpy as np
import pandas as pd

from ..data.source import validate_bars
from ..execution.sizing import compute_orders
from ..strategy.base import Strategy

TRADING_DAYS_PER_YEAR = 252
DAYS_PER_YEAR = 365.25


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
    risk_free_annual: float = 0.0

    @property
    def stats(self) -> dict[str, float]:
        returns = self.equity.pct_change().dropna()
        total = self.equity.iloc[-1] / self.equity.iloc[0] - 1.0

        # Calendar time, not bar count: a walk-forward window can hold any
        # number of bars, and dividing by 252 would silently rescale its CAGR.
        span_days = (self.equity.index[-1] - self.equity.index[0]).days
        years = span_days / DAYS_PER_YEAR if span_days > 0 else 0.0

        drawdown = self.equity / self.equity.cummax() - 1.0

        # Sharpe on *excess* return. With a non-zero risk-free rate, holding
        # cash is no longer a free option, which is the honest comparison.
        daily_rf = (1.0 + self.risk_free_annual) ** (1.0 / TRADING_DAYS_PER_YEAR) - 1.0
        excess = returns - daily_rf
        sharpe = (
            float(excess.mean() / excess.std() * np.sqrt(TRADING_DAYS_PER_YEAR))
            if len(excess) > 1 and excess.std() > 0
            else 0.0
        )

        return {
            "total_return": float(total),
            "cagr": float((1.0 + total) ** (1.0 / years) - 1.0) if years > 0 else 0.0,
            "max_drawdown": float(drawdown.min()),
            "sharpe": sharpe,
            "volatility": float(returns.std() * np.sqrt(TRADING_DAYS_PER_YEAR)) if len(returns) > 1 else 0.0,
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
    rebalance_band: float = 0.0,
    risk_free_annual: float = 0.0,
) -> BacktestResult:
    """Replay `bars` through `strategy`, one bar at a time.

    `fee_bps` and `slippage_bps` are in basis points (1 bp = 0.01%). The
    defaults are deliberately non-zero; pass 0 only to isolate a bug, never to
    report a result.

    `rebalance_band` suppresses trades smaller than that fraction of equity
    (0.02 = 2%). Without it a constant-weight target rebalances every single
    bar, bleeding fees to correct drift nobody asked to correct.
    """
    validate_bars(dict(bars))
    if fee_bps < 0 or slippage_bps < 0:
        raise ValueError("costs cannot be negative")
    if not 0.0 <= rebalance_band < 1.0:
        raise ValueError("rebalance_band must be in [0, 1)")

    index = next(iter(bars.values())).index
    fee_rate = fee_bps / 10_000.0
    slip_rate = slippage_bps / 10_000.0
    daily_rf = (1.0 + risk_free_annual) ** (1.0 / TRADING_DAYS_PER_YEAR) - 1.0

    # Scalar prices come from numpy, not repeated `.iloc` lookups: pandas
    # scalar access dominated the runtime of a walk-forward sweep.
    closes_by_symbol = {s: df["close"].to_numpy(dtype=float) for s, df in bars.items()}
    opens_by_symbol = {s: df["open"].to_numpy(dtype=float) for s, df in bars.items()}

    cash = float(initial_cash)
    positions: dict[str, float] = {symbol: 0.0 for symbol in bars}
    fills: list[Fill] = []
    equity_points: list[float] = []

    for i, _ in enumerate(index):
        equity_points.append(cash + sum(positions[s] * closes_by_symbol[s][i] for s in bars))

        # The final bar has no next open to fill against, so nothing to decide.
        if i + 1 >= len(index):
            continue

        # The only view the strategy ever gets: bars 0..i inclusive.
        history = {symbol: df.iloc[: i + 1] for symbol, df in bars.items()}
        targets = _validate_targets(strategy.on_bar(history), bars)

        cash *= 1.0 + daily_rf  # one day of interest on the cash balance

        fill_ts = index[i + 1]
        opens = {symbol: opens_by_symbol[symbol][i + 1] for symbol in bars}
        equity_at_fill = cash + sum(positions[s] * opens[s] for s in bars)
        if equity_at_fill <= 0:
            continue  # wiped out; nothing left to allocate

        # Shared with the live trader so backtest and production size
        # positions identically. Returns sells first.
        orders = compute_orders(
            targets=targets,
            positions=positions,
            prices=opens,
            equity=equity_at_fill,
            rebalance_band=rebalance_band,
            min_trade_notional=min_trade_notional,
        )

        for order in orders:
            # Slippage always works against us: pay up to buy, sell into the bid.
            direction = 1.0 if order.delta_shares > 0 else -1.0
            price = order.reference_price * (1.0 + direction * slip_rate)
            notional = order.delta_shares * price
            fee = abs(notional) * fee_rate

            cash -= notional + fee
            positions[order.symbol] += order.delta_shares
            fills.append(Fill(fill_ts, order.symbol, order.delta_shares, price, fee))

    return BacktestResult(
        equity=pd.Series(equity_points, index=index, name="equity"),
        fills=fills,
        risk_free_annual=risk_free_annual,
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
