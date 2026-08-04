"""Walk-forward evaluation — the difference between "worked" and "will work".

A single backtest over all history answers the wrong question. Parameters were
chosen knowing how the whole period turned out, so the result measures
hindsight, not edge. Walk-forward instead repeats a strict loop:

    choose parameters on a *training* window
    score them on the *next*, unseen window
    roll forward and repeat

Only the test windows are scored, and they are stitched into one out-of-sample
curve. That curve is the honest one.

Three numbers matter more than the equity line:

- **trials** — how many parameter sets were tried. Literature is blunt about
  this: about three trials is enough to manufacture something that looks
  significant with no edge. More trials, weaker evidence.
- **retention** — out-of-sample Sharpe over in-sample Sharpe. A healthy
  strategy keeps roughly 50–70%. Near zero or negative means the in-sample
  result was fitted noise.
- **deflated Sharpe** — the probability the Sharpe survives once you correct
  for how many trials it took to find it, plus the return distribution's skew
  and fat tails. Below ~0.95 is not evidence.

References: Bailey & López de Prado, "The Deflated Sharpe Ratio" (2014).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import NormalDist
from typing import Callable, Mapping, Sequence

import pandas as pd

from ..strategy.base import Strategy
from .engine import TRADING_DAYS_PER_YEAR, BacktestResult, run

EULER_MASCHERONI = 0.5772156649015329

# What a healthy strategy keeps out-of-sample, per the walk-forward literature.
HEALTHY_RETENTION = 0.5


def slice_result(result: BacktestResult, start: pd.Timestamp, end: pd.Timestamp) -> BacktestResult:
    """Restrict a completed backtest to a window, for scoring that window only.

    The run itself always starts at the beginning of history so indicators are
    warm; scoring then looks at just the slice. Cutting the *run* instead would
    leave a 50-bar moving average flat through the first 50 bars of every test
    window and quietly understate the strategy.
    """
    equity = result.equity.loc[start:end]
    fills = [f for f in result.fills if start <= f.ts <= end]
    return BacktestResult(equity=equity, fills=fills, risk_free_annual=result.risk_free_annual)


def expected_max_sharpe(trial_sharpes: Sequence[float]) -> float:
    """Sharpe the *best* of N random, edge-less trials would show by luck.

    This is the bar a real result has to clear. It rises with the number of
    trials, which is exactly why unreported trials are a form of lying.
    """
    n = len(trial_sharpes)
    if n < 2:
        return 0.0

    mean = sum(trial_sharpes) / n
    variance = sum((s - mean) ** 2 for s in trial_sharpes) / (n - 1)
    if variance <= 0:
        return 0.0

    normal = NormalDist()
    return math.sqrt(variance) * (
        (1.0 - EULER_MASCHERONI) * normal.inv_cdf(1.0 - 1.0 / n)
        + EULER_MASCHERONI * normal.inv_cdf(1.0 - 1.0 / (n * math.e))
    )


def deflated_sharpe(
    observed_sharpe: float,
    trial_sharpes: Sequence[float],
    n_observations: int,
    skew: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Probability the observed Sharpe reflects skill rather than selection.

    All Sharpes are per-period (not annualised) — mixing the two silently
    inflates the result by sqrt(252).
    """
    if n_observations < 2:
        return 0.0

    threshold = expected_max_sharpe(trial_sharpes)
    denominator = 1.0 - skew * observed_sharpe + (kurtosis - 1.0) / 4.0 * observed_sharpe**2
    if denominator <= 0:
        return 0.0

    z = (observed_sharpe - threshold) * math.sqrt(n_observations - 1) / math.sqrt(denominator)
    return NormalDist().cdf(z)


def _up_to(bars: Mapping[str, pd.DataFrame], end: pd.Timestamp) -> dict[str, pd.DataFrame]:
    """History from the start through `end`.

    Runs always begin at the start of history so indicators are warm, but
    simulating *past* the window being scored is wasted work — and on a
    parameter sweep that waste is most of the runtime.
    """
    return {symbol: df.loc[:end] for symbol, df in bars.items()}


@dataclass
class Fold:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    chosen_params: dict
    train_sharpe: float
    test_sharpe: float
    test_return: float


@dataclass
class WalkForwardResult:
    folds: list[Fold] = field(default_factory=list)
    oos_equity: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    trials: int = 0
    trial_sharpes: list[float] = field(default_factory=list)
    # Per-configuration Sharpes, keyed by parameter set. See `config_sharpes`
    # below for why the raw evaluation count is the wrong N for a deflated
    # Sharpe when the same grid is re-tested every fold.
    sharpes_by_config: dict[str, list[float]] = field(default_factory=dict)

    @property
    def config_sharpes(self) -> list[float]:
        """One Sharpe per distinct parameter set, averaged over folds.

        `trial_sharpes` holds folds x grid entries — 136 for a grid of 8 over
        17 folds. Feeding that to the deflated Sharpe overstates N: only 8
        distinct configurations were ever searched, and re-testing them on
        overlapping windows is not 136 independent attempts. It also muddles
        the variance term, which then mixes spread-across-parameters with
        spread-across-time.

        Which N is correct is genuinely arguable — the walk-forward procedure
        makes a fresh selection every fold — so both are reported and the
        verdict uses the conservative one. Silently switching to whichever
        number reads better would be the actual error.
        """
        return [sum(v) / len(v) for v in self.sharpes_by_config.values() if v]

    @property
    def summary(self) -> dict[str, float]:
        if not self.folds or self.oos_equity.empty:
            return {}

        returns = self.oos_equity.pct_change().dropna()
        per_period_sharpe = (
            float(returns.mean() / returns.std()) if len(returns) > 1 and returns.std() > 0 else 0.0
        )

        train = [f.train_sharpe for f in self.folds]
        test = [f.test_sharpe for f in self.folds]
        mean_train = sum(train) / len(train)
        mean_test = sum(test) / len(test)

        drawdown = self.oos_equity / self.oos_equity.cummax() - 1.0

        # Both deflated Sharpes share the return distribution's shape; only
        # the trial count differs.
        skew = float(returns.skew()) if len(returns) > 2 else 0.0
        kurtosis = float(returns.kurt()) + 3.0 if len(returns) > 3 else 3.0

        return {
            "folds": float(len(self.folds)),
            "trials": float(self.trials),
            "oos_total_return": float(self.oos_equity.iloc[-1] / self.oos_equity.iloc[0] - 1.0),
            "oos_sharpe": per_period_sharpe * math.sqrt(TRADING_DAYS_PER_YEAR),
            "oos_max_drawdown": float(drawdown.min()),
            "mean_train_sharpe": mean_train,
            "mean_test_sharpe": mean_test,
            # Negative in-sample Sharpe makes a ratio meaningless, not "good".
            "retention": (mean_test / mean_train) if mean_train > 0 else 0.0,
            "positive_folds": float(sum(1 for s in test if s > 0)) / len(test),
            "configs": float(len(self.sharpes_by_config)),
            # Conservative: N = every evaluation performed. Harsh, because
            # re-testing one grid each fold is not N independent searches.
            "deflated_sharpe": deflated_sharpe(
                per_period_sharpe,
                self.trial_sharpes,
                len(returns),
                skew=skew,
                kurtosis=kurtosis,
            ),
            # Lenient: N = distinct parameter sets searched.
            "deflated_sharpe_by_config": deflated_sharpe(
                per_period_sharpe,
                self.config_sharpes,
                len(returns),
                skew=skew,
                kurtosis=kurtosis,
            ),
        }

    @property
    def verdict(self) -> str:
        s = self.summary
        if not s:
            return "no folds evaluated"
        if s["mean_test_sharpe"] <= 0:
            return "REJECT — no out-of-sample edge"

        strict, lenient = s["deflated_sharpe"], s["deflated_sharpe_by_config"]
        if strict < 0.95:
            # Flag disagreement rather than hide it: when the two trial counts
            # straddle the threshold, the verdict rests on a debatable choice
            # of N and deserves to be looked at, not filed under REJECT.
            if lenient >= 0.95:
                return (
                    f"BORDERLINE — deflated Sharpe {strict:.2f} counting every evaluation, "
                    f"{lenient:.2f} counting distinct configs. The verdict depends on which "
                    "trial count you accept; re-test on unseen symbols before believing it"
                )
            return f"REJECT — deflated Sharpe {strict:.2f} < 0.95, indistinguishable from selection luck"

        if s["retention"] < HEALTHY_RETENTION:
            return f"WEAK — keeps only {s['retention']:.0%} of in-sample Sharpe"
        return "SURVIVES — worth more scrutiny, still not proof"


def walk_forward(
    bars: Mapping[str, pd.DataFrame],
    factory: Callable[[dict], Strategy],
    grid: Sequence[dict],
    train_bars: int = 756,  # ~3 years
    test_bars: int = 126,  # ~6 months
    initial_cash: float = 100_000.0,
    **run_kwargs,
) -> WalkForwardResult:
    """Roll train/test windows forward, selecting parameters on train only."""
    if not grid:
        raise ValueError("grid must contain at least one parameter set")

    index = next(iter(bars.values())).index
    result = WalkForwardResult()
    oos_returns: list[pd.Series] = []

    start = train_bars
    while start + test_bars <= len(index):
        train_start, train_end = index[start - train_bars], index[start - 1]
        test_start, test_end = index[start], index[start + test_bars - 1]

        # Select on the training window only. Every run starts at the beginning
        # of history for warm indicators, then is scored on the window.
        train_bars_view = _up_to(bars, train_end)
        best_params, best_sharpe = None, -math.inf
        for params in grid:
            scored = slice_result(
                run(train_bars_view, factory(params), initial_cash=initial_cash, **run_kwargs),
                train_start,
                train_end,
            )
            sharpe = scored.stats["sharpe"]
            result.trials += 1
            result.sharpes_by_config.setdefault(repr(sorted(params.items())), []).append(
                sharpe / math.sqrt(TRADING_DAYS_PER_YEAR)
            )
            result.trial_sharpes.append(sharpe / math.sqrt(TRADING_DAYS_PER_YEAR))
            if sharpe > best_sharpe:
                best_params, best_sharpe = params, sharpe

        tested = slice_result(
            run(_up_to(bars, test_end), factory(best_params), initial_cash=initial_cash, **run_kwargs),
            test_start,
            test_end,
        )
        stats = tested.stats

        result.folds.append(
            Fold(
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
                chosen_params=dict(best_params),
                train_sharpe=best_sharpe,
                test_sharpe=stats["sharpe"],
                test_return=stats["total_return"],
            )
        )
        oos_returns.append(tested.equity.pct_change().dropna())
        start += test_bars

    if oos_returns:
        stitched = pd.concat(oos_returns)
        stitched = stitched[~stitched.index.duplicated(keep="first")].sort_index()
        result.oos_equity = initial_cash * (1.0 + stitched).cumprod()

    return result
