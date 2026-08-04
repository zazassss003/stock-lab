# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

A personal research platform for US equities, built in four gated stages:
data → features → backtest → paper execution. Python 3.10, pandas.

## Non-negotiable rules

These exist because violating them produces backtests that look profitable and
lose real money. Do not relax them for convenience.

1. **No look-ahead, ever.** A strategy may only see bars at or before the
   current timestamp. `backtest/engine.py` enforces this by slicing history to
   `[:i+1]` and filling orders at the *next* bar's open. Never pass a full
   DataFrame to strategy code. Never use `.shift(-n)`, future-window rolling,
   or full-series `fit()` inside a strategy.
2. **No LLM in the trade path.** Agents build this system; they are never part
   of running it. The live loop is deterministic Python on a scheduler. Given
   the same inputs it must produce byte-identical decisions, replayable months
   later.
3. **Costs are always modelled.** Every backtest applies fees and slippage.
   A zero-cost backtest is not a result, it is a bug.
4. **Paper only.** `execution/` targets Alpaca's paper endpoint. Do not add a
   live-money endpoint, credential, or order path. That switch is the author's
   to flip, by hand, much later.
5. **Tuning data and evaluation data are disjoint.** Anything tuned on a period
   must be reported on a later, untouched period. Say which is which in output.

## Layout

```
src/stocklab/
  data/        DataSource protocol + adapters (yfinance now, Alpaca later)
  features/    indicator functions — pure, stateless, no I/O
  strategy/    Strategy protocol + implementations; history-only input
  backtest/    the engine — treat as the test suite for every strategy
  execution/   Broker protocol; paper adapter only
tests/         pytest; test_no_lookahead.py is the load-bearing one
```

## Conventions

- Type hints on public functions. `pandas` DataFrames indexed by tz-aware UTC
  timestamps, columns lowercase: `open, high, low, close, volume`.
- Features are pure functions `(df) -> Series`. No hidden state, no network.
- New strategy = new file in `strategy/`, implementing the `Strategy` protocol.
  It gets evaluated by the engine or it does not get merged.
- Run tests with `py -3 -m pytest`.
