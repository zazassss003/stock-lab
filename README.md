# stock-lab

A personal US-equities research platform: track, analyse, backtest, and
eventually paper-trade — built in four gated stages.

**Not investment advice, and not a money machine.** It is an instrument for
finding out whether an idea survives honest measurement. Most do not.

## Setup

```bash
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
py -3 -m pytest
```

## Staying current

Data runs from 2015 to **the latest closed session** — no pinned end date. Pin
one only when you deliberately want a frozen window for a reproducible study.

```bash
py -3 scripts/update.py SPY
```

Refreshes the data and rebuilds the dashboard. Built to run unattended: it logs
one line per run to `update.log` and exits non-zero when the data is stale,
because the dangerous failure is the silent one that leaves last week's
dashboard in place looking perfectly healthy.

Three rules keep a tracker honest, each learned from a real failure:

- **The cache is keyed by symbol, not by date range.** Keying it by
  `symbol_start_end` means a moving end date re-downloads all of history every
  single day and never reuses a thing.
- **An unclosed session is not a bar.** Its "close" is still moving, so a
  signal derived from it changes retroactively once the session really closes.
  Dropped, based on the session date against the current time in market hours.
- **Bars with no prices are dropped *and reported*.** Yahoo publishes the
  newest bar's volume before its adjustment factor, so with `auto_adjust=True`
  that row can arrive with real volume and NaN prices. Dropping it silently is
  how a broken feed passes for a quiet market. A recent one is re-fetched each
  run until the provider fixes it; the last seven days are always re-fetched,
  so a missed or late day self-heals on the next run.

## Dashboard

```bash
py -3 scripts/dashboard.py SPY
```

Writes `dashboard.html` — a standalone page with no server and no external
requests, openable straight from disk. Equity curve, drawdown, price with
moving averages, RSI, a strategy comparison table and the full trade list.

Design notes, since they are decisions rather than taste:

- **Drawdown is its own chart, not a second y-axis.** Two y-scales on one plot
  align arbitrarily and invent correlations that are not in the data. The two
  charts share an x-axis and a linked crosshair instead.
- **Colour follows the entity.** A strategy keeps its hue everywhere it
  appears — curve, drawdown, legend, table — so filtering never repaints it.
- **Every chart has a table twin.** No value is reachable only by hovering.
  This is a requirement, not a nicety: the SMA 50 hue measures 2.74:1 against
  the light surface, and the price chart suppresses its direct labels where the
  three lines converge — without the table that series would be encoded by
  colour alone. The palette is otherwise validated in both modes (worst
  adjacent CVD ΔE 9.2 light / 9.4 dark against an ≥8 target).
- Light and dark are both designed sets from a CVD-validated palette, not an
  automatic inversion.

## Validating a strategy

```bash
py -3 scripts/research.py SPY
```

Walk-forward: pick parameters on a 3-year training window, score them on the
next unseen 6 months, roll, repeat. Only test windows are scored, and they are
stitched into one out-of-sample curve.

Read three columns before the return:

| Column | Meaning | Bar |
|---|---|---|
| `trials` | parameter sets searched | more trials = weaker claim |
| `keep` | out-of-sample Sharpe / in-sample Sharpe | 50–70% is healthy |
| `DSR` | deflated Sharpe — probability it is not selection luck | below 0.95 is not evidence |

The deflated Sharpe exists because roughly **three trials is enough to
manufacture a strategy that looks significant with no edge**. It raises the bar
in proportion to how hard you looked. The benchmark row — buy & hold over the
same out-of-sample window — is there because a strategy that beats zero but
loses to doing nothing is not an edge, just an expensive way to own less market.

**Current status: every strategy in this repo is REJECTED**, and all five lose
to buy & hold over the same out-of-sample window — the best of them returns
roughly half. That is the harness working, and it is the normal outcome. The
honest nuance: Donchian breakout earns a better Sharpe with a third of the
drawdown, so it is not *worthless* — it is just not distinguishable from luck
at 68 trials, which is a different claim from "it doesn't work".

## Strategies

| File | Idea |
|---|---|
| `buy_and_hold.py` | benchmark; everything must beat this after costs |
| `sma_cross.py` | fast/slow moving-average crossover |
| `momentum.py` | time-series momentum — long while trailing return is positive |
| `vol_target.py` | momentum sized by inverse realised volatility |
| `rsi_reversion.py` | buy oversold, exit on the bounce |
| `donchian.py` | Turtle channel breakout |

## Execution — the safety model

```bash
py -3 scripts/trade.py SPY              # simulated broker, dry run
py -3 scripts/trade.py SPY --alpaca     # Alpaca paper account, dry run
py -3 scripts/trade.py SPY --alpaca --live   # submits paper orders
```

Four layers, every one defaulting to *not trading*:

1. **`enable_trading` is False by default.** The loop computes and journals
   everything and submits nothing. Run it this way for weeks; the journal is
   the record of what it would have done.
2. **Kill switch file.** `touch HALT` in the repo root stops the loop on its
   next cycle — no code edit, no restart, doable in a panic from any device.
3. **Risk limits per order** — position notional, orders per day, daily loss.
   A breach blocks the whole cycle rather than shrinking the order, because a
   strategy asking for something absurd is a bug, not a sizing problem.
4. **`journal.jsonl`** records every cycle for reconciliation against what the
   broker actually did.

The Alpaca adapter **refuses any non-paper endpoint in its constructor**, so
this cannot be pointed at real money by editing a config string.

Backtest and live share one sizing function (`execution/sizing.py`). If they
diverged, every backtest number would describe a system that isn't the one
running — and you would only find out months later, as unexplained
underperformance.

## The four stages

Each stage ships before the next starts. The temptation is to skip to stage 3;
that is how people build profitable-looking systems that lose money.

### Stage 1 — Data (start here)
Ingest daily OHLCV, store to parquet, and prove it is clean: no gaps on trading
days, no duplicate timestamps, split/dividend adjusted, consistent timezone.
Every result downstream inherits these bugs, so this stage has tests before it
has features.

**Done when:** a year of data for 20 symbols loads offline and the data-quality
suite is green.

### Stage 2 — The backtest harness
The single most important component — it is the test suite for every idea you
will ever have. It must make look-ahead bias *structurally impossible* and must
model fees and slippage. Build it before writing any strategy.

**Done when:** a deliberately cheating strategy cannot beat the engine, and a
known-flat strategy (buy and hold) reproduces the benchmark within costs.

### Stage 3 — Strategies
Now ideas are cheap to write and, more importantly, cheap to *reject*. Tune on
one period, report on a later untouched one. Expect most ideas to die here.
That is the system working.

**Done when:** an idea survives out-of-sample evaluation with costs included.

### Stage 4 — Paper execution
Broker adapter behind an interface, pointed at Alpaca's paper endpoint, with a
kill switch and hard position limits from the first commit. Run for months.
Compare live fills against what the backtest predicted — the gap between those
two numbers is the real lesson of the project.

## Two loops, do not confuse them

| | Agent loop | Trading loop |
|---|---|---|
| What | Claude writing and testing this code | The program fetching prices and deciding |
| When | While developing | Every N minutes, unattended |
| Made of | LLM, exploratory | **Deterministic Python. Zero LLM.** |

An LLM in the live path is slow, nondeterministic and unauditable — you could
never answer "why did it buy at 10:31?"
