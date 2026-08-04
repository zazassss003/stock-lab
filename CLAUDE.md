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
6. **`docs/SPEC.zh-TW.md` ships with the change that affects it.** It is the
   owner-facing manual, in Traditional Chinese, written for someone who does
   not read code. A behaviour change, a new script, a renamed flag, a changed
   schedule or default — all require updating it in the same commit, including
   its version table. A stale manual is worse than none: it reads as
   authoritative while being wrong.

## Layout

```
src/stocklab/
  data/        DataSource protocol + adapters (yfinance now, Alpaca later)
  features/    indicator functions — pure, stateless, no I/O
  strategy/    Strategy protocol + implementations; history-only input
  backtest/    the engine — treat as the test suite for every strategy
  execution/   Broker protocol; paper adapter only
  report/      dashboard payload + self-contained HTML template
  research/    the briefing page; the one place LLM output is displayed
tests/         pytest; test_no_lookahead.py is the load-bearing one
docs/          SPEC.zh-TW.md — owner-facing manual (see rule 6)
```

`research/` is the exception that proves rule 2, and it stays legal by being a
dead end. Nothing in `strategy/`, `backtest/`, or `execution/` may import it;
model-written notes land in `research_store/notes.json` for a human to read, and
the renderer is the only consumer. If a change would let note content reach a
signal, a position size, or an order, it is the wrong change — say so rather
than finding a way to do it.

## Conventions

- Type hints on public functions. `pandas` DataFrames indexed by tz-aware UTC
  timestamps, columns lowercase: `open, high, low, close, volume`.
- Features are pure functions `(df) -> Series`. No hidden state, no network.
- New strategy = new file in `strategy/`, implementing the `Strategy` protocol.
  It gets evaluated by the engine or it does not get merged.
- Run tests with `py -3 -m pytest`.
