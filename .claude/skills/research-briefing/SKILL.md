---
name: research-briefing
description: Refresh the stock-lab research briefing page (research.html) for the tracked universe — pulling latest bars, running equity-research analysis per symbol, writing notes to research_store/notes.json, and rendering. Use this whenever the user asks to update, refresh, rebuild, or regenerate the briefing, the research page, or the notes; whenever they ask "what's changed" or "catch me up" on the tracked stocks or watchlist; whenever a tracked name has reported earnings; and whenever a note is flagged stale. Also use it when they ask for research on a specific tracked ticker, even if they don't mention the page by name.
---

# Research briefing

Rebuilds `research.html`: a per-symbol reading surface for the tracked universe,
combining deterministic price facts with written research notes.

## The one boundary that matters

This briefing is **outside the trade path**. `CLAUDE.md` rule 2 forbids an LLM
anywhere near a trading decision, and this workflow stays legal because the
model's output lands in a JSON file a human reads — never in a signal, an order,
or anything `strategy/`, `backtest/`, or `execution/` imports.

If a request would have you feed note content into a strategy, size a position
from a note, or auto-act on a catalyst, stop and say so. The briefing informs the
author; it does not decide.

## Workflow

### 1. Refresh the bars first

```bash
./.venv/Scripts/python.exe scripts/update.py
```

Notes written against stale prices describe a market that no longer exists, and
the staleness badge cannot catch that — it compares against whatever the store
holds. Get the data right before writing anything about it.

Use the venv interpreter, not `py -3`: the global Python lacks `pyarrow` and the
parquet cache will not load.

### 2. Get the deterministic context

```bash
./.venv/Scripts/python.exe scripts/brief.py --prompts
```

This prints one block per symbol carrying that name's authoritative numbers —
returns, excess vs SPY, 52-week position, RSI, realised vol, trend. Restrict to
specific names by passing them: `--prompts NVDA AAPL`.

These figures are computed from the same indicator functions the backtest uses.
Treat them as ground truth. If your research turns up a number that contradicts
them, the research is wrong, not the strip — and that contradiction is itself
worth reporting to the user.

### 3. Research each symbol

Run the equity-research skill per name:

```
/earnings <SYMBOL> latest quarter
```

If the `equity-research` plugin is not loaded, fall back to direct web research
and say so plainly rather than producing a note that looks like skill output.
The `source` field exists to make this distinction survivable months later.

What separates a useful note from filler:

- **The thesis names the disagreement.** A company can beat on every line and
  still fall 7% — that gap between results and reaction is the interesting part.
  A note that only restates the press release has told the reader nothing they
  could not get faster elsewhere.
- **Risks are falsifiers, not caveats.** "Competition is intense" is filler.
  "A single quarter of Cloud below ~50% growth reprices this" is a risk, because
  you could check it and be proven wrong.
- **Catalysts are forward-looking and dated.** Past events belong in the thesis.
- **Watch items are metrics, not themes.** Something with a number next quarter.

### 4. Write the notes

Append to `research_store/notes.json` under `notes`, keyed by symbol. The schema
is `stocklab.research.notes.Note`:

| Field | Notes |
|---|---|
| `symbol` | Must be in `universe.SYMBOLS` |
| `headline` | One line, specific — the claim, not the topic |
| `thesis` | 3-6 sentences |
| `written` | ISO8601 UTC |
| `source` | e.g. `equity-research:/earnings` or `claude-opus-5:web` |
| `close_at_write` | The `last close` from step 2, exactly |
| `risks` | At least two, each falsifiable |
| `catalysts` | `[{date, label}]`, future-dated |
| `watch` | Metrics to check next quarter |

`close_at_write` drives the staleness badge. Getting it wrong silently disables
the mechanism that stops an old opinion passing as a current one, so copy it from
the prompt block rather than looking it up again.

### 5. Render and report

```bash
./.venv/Scripts/python.exe scripts/brief.py
```

Report the coverage count, which names are stale, and the newest bar date. If the
script warns that bars are old, surface that — a clean-looking page built on
week-old prices is worse than an obviously broken one.

## Scope

Only names in `stocklab.universe.SYMBOLS`. SPY is the benchmark leg and carries
no note; it exists so every other row has something to be measured against.

If the user asks about a ticker outside the list, say it is not tracked and offer
to add it to `universe.py` — but flag that the list's docstring warns against
backtesting on it, since the membership was chosen with hindsight.

## When notes go stale

A note is flagged automatically once price has moved 10% since it was written, or
after 45 days. Both thresholds live in `stocklab.research.notes`.

Stale flags are the normal signal to rerun a name, and the honest default is to
rewrite rather than patch: a note is an argument made at a moment, and editing
the conclusion while keeping the reasoning produces something that never actually
held together.
