"""The backtest engine — the test suite for every idea in this repo.

Two properties matter more than anything else here:

1. **Look-ahead is structurally impossible.** The strategy is handed
   ``df.iloc[:i+1]`` — a slice that physically cannot contain a future bar —
   and the resulting orders fill at bar ``i+1``'s open. A strategy cannot act
   on a price it could not have known, because it was never given one.

2. **Costs always apply.** The cost model is a parameter with a non-zero
   default. A backtest reporting zero costs is a bug, not a good result.

Keep both properties intact. Nearly every "too good to be true" backtest in
existence is one of these two rules quietly broken.

Modelling assumptions, stated so they are arguable rather than hidden:

- **Fractional shares by default.** Target weights convert to fractional share
  counts. True at Alpaca for liquid US equities; false at many brokers — pass
  `qty_increment=1.0` for a broker that only fills whole shares.
- **Unlimited liquidity at the next open**, plus fixed slippage. Fine for
  large-cap ETFs, optimistic for anything thin.
- **Cash earns `risk_free_annual`**, default 0. Leaving it at 0 penalises
  strategies that sit in cash, so it is conservative rather than flattering —
  but for an honest comparison against a real alternative, set it.
- **The account is already in dollars** unless the cost model says otherwise.
  With a model that prices currency (see `costs.py`), `initial_cash` is what
  you committed and the account starts with less — the conversion is charged
  before the first trade, which is when it really happens.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import numpy as np
import pandas as pd

from ..data.source import validate_bars
from ..execution.sizing import DEFAULT_CASH_BUFFER, compute_orders
from ..strategy.base import Strategy
from .costs import FLAT_DEFAULT, CostModel

TRADING_DAYS_PER_YEAR = 252
DAYS_PER_YEAR = 365.25


@dataclass(frozen=True)
class Fill:
    ts: pd.Timestamp
    symbol: str
    qty: float  # positive = bought, negative = sold, in shares
    price: float  # execution price, slippage included
    fee: float  # commission + regulatory, the total billed
    # Broken out so a report can say *which* cost hurt. `fee` stays the total
    # because that is what every existing consumer reads.
    commission: float = 0.0
    regulatory: float = 0.0
    reference_price: float = 0.0  # the price before slippage


@dataclass
class BacktestResult:
    equity: pd.Series
    fills: list[Fill] = field(default_factory=list)
    risk_free_annual: float = 0.0
    costs: CostModel = FLAT_DEFAULT
    # What was committed to the account, before the cost of getting it there.
    # None for a sliced window: no money crossed a currency line mid-history,
    # so charging that window for a conversion would be invention.
    gross_capital: float | None = None

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

        # Slippage is paid in the price, not billed, so it never appears on a
        # statement — which is exactly why it has to be reconstructed here.
        # Left out of a cost total it is a real loss nobody can point at.
        slippage = float(
            sum(abs(f.qty) * abs(f.price - f.reference_price) for f in self.fills if f.reference_price)
        )

        return {
            "total_return": float(total),
            "cagr": float((1.0 + total) ** (1.0 / years) - 1.0) if years > 0 else 0.0,
            "max_drawdown": float(drawdown.min()),
            "sharpe": sharpe,
            "volatility": float(returns.std() * np.sqrt(TRADING_DAYS_PER_YEAR)) if len(returns) > 1 else 0.0,
            "n_fills": float(len(self.fills)),
            "total_fees": float(sum(f.fee for f in self.fills)),
            "total_commission": float(sum(f.commission for f in self.fills)),
            "total_regulatory": float(sum(f.regulatory for f in self.fills)),
            "total_slippage": slippage,
            "fx_cost": self.fx_cost,
            # What the account returned to whoever funded it, after paying to
            # get the money in and out again. Equal to `total_return` whenever
            # currency is not modelled, which is the honest default.
            "net_return": self.net_return,
        }

    @property
    def fx_cost(self) -> float:
        """Both currency conversions: funding the account, and closing it out."""
        if self.gross_capital is None:
            return 0.0
        entry = self.costs.fx_cost(self.gross_capital)
        final = float(self.equity.iloc[-1])
        return entry + self.costs.fx_cost(final)

    @property
    def net_return(self) -> float:
        """Return measured against money committed, not money that arrived."""
        total = float(self.equity.iloc[-1] / self.equity.iloc[0] - 1.0)
        if self.gross_capital is None or self.gross_capital <= 0:
            return total

        final = float(self.equity.iloc[-1])
        repatriated = final - self.costs.fx_cost(final)
        return repatriated / self.gross_capital - 1.0


def run(
    bars: Mapping[str, pd.DataFrame],
    strategy: Strategy,
    initial_cash: float = 100_000.0,
    costs: CostModel = FLAT_DEFAULT,
    min_trade_notional: float = 1.0,
    rebalance_band: float = 0.0,
    risk_free_annual: float = 0.0,
    qty_increment: float = 0.0,
) -> BacktestResult:
    """Replay `bars` through `strategy`, one bar at a time.

    `costs` is a `CostModel` (see `costs.py`) — commission, statutory fees,
    currency and slippage. The default charges a flat 1bp plus 5bp slippage;
    pass a broker preset for a result about a specific account, and
    `ZERO_COST_FOR_DEBUGGING` only to isolate a bug, never to report.

    `rebalance_band` suppresses trades smaller than that fraction of equity
    (0.02 = 2%). Without it a constant-weight target rebalances every single
    bar, bleeding fees to correct drift nobody asked to correct.

    `qty_increment` is the smallest tradeable lot: 0 allows fractional shares,
    1.0 forces whole ones. Fractional is the optimistic assumption — it lets
    every target be hit exactly — so a broker that cannot do it should say so.
    """
    validate_bars(dict(bars))
    if not 0.0 <= rebalance_band < 1.0:
        raise ValueError("rebalance_band must be in [0, 1)")
    if qty_increment < 0:
        raise ValueError("qty_increment cannot be negative")

    index = next(iter(bars.values())).index
    daily_rf = (1.0 + risk_free_annual) ** (1.0 / TRADING_DAYS_PER_YEAR) - 1.0

    # Scalar prices come from numpy, not repeated `.iloc` lookups: pandas
    # scalar access dominated the runtime of a walk-forward sweep.
    closes_by_symbol = {s: df["close"].to_numpy(dtype=float) for s, df in bars.items()}
    opens_by_symbol = {s: df["open"].to_numpy(dtype=float) for s, df in bars.items()}

    # Converting the money is the first thing that happens to a foreign-funded
    # account, and it happens whether or not a single trade follows. Charging
    # it up front means the equity curve starts where the account really did.
    cash = float(initial_cash) - costs.fx_cost(float(initial_cash))
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
            cash_buffer=max(DEFAULT_CASH_BUFFER, costs.min_cash_buffer()),
            qty_increment=qty_increment,
        )

        for order in orders:
            price = costs.fill_price(order.reference_price, order.delta_shares)
            notional = order.delta_shares * price
            commission, regulatory = costs.trade_cost(order.delta_shares, price)
            fee = commission + regulatory

            cash -= notional + fee
            positions[order.symbol] += order.delta_shares
            fills.append(
                Fill(
                    ts=fill_ts,
                    symbol=order.symbol,
                    qty=order.delta_shares,
                    price=price,
                    fee=fee,
                    commission=commission,
                    regulatory=regulatory,
                    reference_price=order.reference_price,
                )
            )

    return BacktestResult(
        equity=pd.Series(equity_points, index=index, name="equity"),
        fills=fills,
        risk_free_annual=risk_free_annual,
        costs=costs,
        gross_capital=float(initial_cash),
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
