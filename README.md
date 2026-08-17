# stock-lab

A personal US-equities research platform: track, analyse, backtest, and
eventually paper-trade — built in four gated stages.

**Not investment advice, and not a money machine.** It is an instrument for
finding out whether an idea survives honest measurement. Most do not.

📘 **使用說明（繁體中文）：[docs/SPEC.zh-TW.md](docs/SPEC.zh-TW.md)** — the
owner-facing manual: what to run, how to read every number, what to do when
something looks wrong. Written for a non-programmer. Keep it in sync (CLAUDE.md
rule 6); this README is the technical companion, not a substitute.

## Setup

```bash
py -3 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
py -3 -m pytest
```

## Tracked universe

`src/stocklab/universe.py` — SPY as the benchmark plus the ten most profitable
US companies as of 2026: GOOGL, NVDA, AAPL, MSFT, BRK-B, META, AMZN, JPM, XOM,
BAC.

**That list is survivorship-biased and must not be backtested.** These names
were selected *because* they won; the 2015 version of the list held different
companies, some of which since did badly. Tracking them forward is fine — that
is what a watchlist is — but any strategy result measured on them is flattered
by hindsight. Use SPY, or reconstruct historical index membership, for numbers
you can believe. The warning is repeated in the module docstring and on the
dashboard itself, so it cannot be discovered by accident later.

## Staying current

Data runs from 2015 to **the latest closed session** — no pinned end date. Pin
one only when you deliberately want a frozen window for a reproducible study.

```bash
py -3 scripts/update.py
```

With no arguments it refreshes the whole universe; name symbols to narrow it.
Each symbol is fetched separately on purpose: a shared fetch intersects the
trading calendars, so one short-history name would silently truncate every
other symbol back to its own IPO date. A dead ticker is reported and skipped
rather than sinking the run.

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
requests, openable straight from disk. A watchlist across the whole universe
(last, 1D/1M/1Y change, RSI, trend), then per-symbol detail: equity curve,
drawdown, price with moving averages, RSI, a strategy comparison table and the
full trade list. Switch symbols from the watchlist or the selector.

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

## Research briefing

```bash
py -3 scripts/brief.py            # render research.html
py -3 scripts/brief.py --prompts  # emit the per-symbol prompt pack
```

Writes `research.html` — the tracked list with a deterministic numeric strip per
name (returns, excess vs SPY, 52-week position, RSI, realised vol, trend) joined
to a written research note. Same self-contained contract as the dashboard.

This is the only part of the repo where a language model's output is displayed,
so the boundaries are structural rather than advisory:

- **Generation and rendering are separate processes.** `--prompts` emits the
  deterministic context; an agent runs the equity-research skills and writes
  `research_store/notes.json`; this script renders. The model's output is a
  reviewable file on disk, never something that appears on a page unaudited.
- **Nothing in `strategy/`, `backtest/`, or `execution/` imports
  `research/`.** That is what keeps rule 2 true — there is no code path from a
  sentence a model wrote to an order.
- **Every note stores the price it was written at.** Past 10% drift or 45 days
  the page flags it `STALE`. A note is an argument made at a moment; left alone
  it stops describing reality while still looking authoritative.
- **Model-written strings render through `textContent`, never `innerHTML`.**
  The note store is a file an agent writes; treating it as markup would make the
  page executable by whatever the model emitted.

Numbers come from `features/indicators.py`, so the briefing and the backtest
cannot disagree about what RSI means. When a note contradicts the strip, the
strip is right.

`.claude/skills/research-briefing/` packages the refresh workflow.

`tests/test_research.py` enforces the import boundary by AST-scanning the trade
path, so the dead end is checked rather than merely agreed. It also covers the
two calculations that fail silently: staleness, where a broken rule shows a
confident six-week-old note with no warning, and excess return, where the
dangerous bug is reporting absolute return in a relative column and flattering
every name on the page.

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

## Costs

```bash
py -3 scripts/costs.py SPY --cash 20000
```

Every backtest applies a `CostModel` (`backtest/costs.py`) with four layers,
because a single flat fee models an advertisement rather than a broker:

| Layer | What it is | Where it hides |
|---|---|---|
| Commission | per-share with a floor and a cap, bps, or zero | the floor: `$0.005/share` is `$1` on a 10-share order |
| Regulatory | SEC Section 31 + FINRA TAF, **sells only** | nowhere, but it is always omitted |
| Currency | spread + wire, charged when money crosses | at a bank, so no broker comparison shows it |
| Slippage | decided price vs filled price | in the fill, never on a statement |

The engine default is `broker-neutral` (1bp + 5bp slippage): the dashboard
compares *strategies*, and pricing one specific account would let that broker's
minimum ticket decide which rule looks better. Presets for named schedules
(`ibkr`, `firstrade`, `alpaca`, `subbrokerage`) exist for the other question —
what a particular account would have returned — and `scripts/costs.py` runs all
of them side by side.

`stats` breaks the layers out (`total_commission`, `total_regulatory`,
`total_slippage`, `fx_cost`) so a result can say which one hurt. `net_return`
measures against money committed rather than money that arrived; it equals
`total_return` for any model that does not price currency, which is an
understatement rather than a measurement.

`ZERO_COST_FOR_DEBUGGING` exists to isolate a bug by subtraction. Its name is
long on purpose — a zero-cost backtest is not a good result, it is a broken one.

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

Two members of the `Broker` protocol exist for venues this repo does not yet
talk to, and both default to the permissive answer:

- `qty_increment` — the smallest tradeable lot. Alpaca fills fractions and
  reports `0.0`; a whole-share venue reports `1.0` and sizing rounds toward
  zero, so a constrained account under-trades rather than overdrawing.
- `is_ready()` — consulted before the strategy is called. A broker behind a
  local gateway can be absent without raising, and "the gateway was down" must
  never be journalled as "the strategy wanted no change": those records are
  identical and mean opposite things.

See `docs/BROKER-IBKR-ASSESSMENT.md` for what a second adapter would take.

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
