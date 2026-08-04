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
