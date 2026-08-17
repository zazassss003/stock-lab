# Adding IBKR as a second broker — interface assessment, 2026-08-05

**Question asked:** what in `execution/` has to change before an Interactive
Brokers adapter can sit alongside `AlpacaPaperBroker`, and can the existing
`Broker` protocol take it unmodified?

**Answer:** the protocol needed two additions, both made in this change. The
adapter itself is not written, and the reason is at the bottom.

---

## Why IBKR at all

Not commission. On a $20,000 account run through `scripts/costs.py`, IBKR's
$1-minimum ticket costs more than a commission-free broker on every trading
profile tested. The reason to care about IBKR is the layer underneath:

| | funding an account from Taiwan | official API |
|---|---|---|
| Alpaca | commission-free, eligibility unconfirmed | yes, REST, sandbox free |
| IBKR | in-account FX at ~0.5bp + $2 | yes, four of them |
| Firstrade | bank wire + ~0.3% retail spread | **none** — reverse-engineered only |
| sub-brokerage | no wire needed, retail FX rate | one vendor, US support unverified |

`scripts/costs.py` puts numbers on the first column: over eleven years of SPY
buy-and-hold on $20,000, the retail-FX path costs about $420 in currency
against roughly $10 through an in-account conversion. That gap is larger than
the entire commission difference on any profile in the table, and it is the
single reason a second adapter is worth the work at all.

The second column is why the list is short. A broker without an official API
cannot be automated safely at any commission.

---

## Where the two brokers actually differ

Four differences matter to code in this repository. Two were already latent
bugs against Alpaca; IBKR just makes them unavoidable.

### 1. Fractional shares — an assumption, now a declaration

`engine.py` documented "fractional shares — true at Alpaca, false at many
brokers" and then hard-coded the optimistic case. `compute_orders` produced
`delta_shares = 12.6473…` and nothing ever rounded it.

Against Alpaca that is correct. Against a whole-share venue every order is
silently wrong, and — worse — the *backtest* stays fractional, so live and
simulated results diverge for a reason no report shows.

**Changed.** `Broker.qty_increment` declares the venue's lot size,
`compute_orders(qty_increment=…)` rounds onto it, and `engine.run` takes the
same parameter so a backtest can be run under the same constraint. Rounding is
toward zero, so a constrained account under-trades rather than overshooting a
target into an overdraft or a short. `AlpacaPaperBroker.qty_increment = 0.0`
keeps every existing number identical.

IBKR does support fractional US equities, but only on permissioned accounts and
with a different order field. An adapter should report `1.0` until that
permission is confirmed on the actual account — the assumption that costs
nothing when wrong.

### 2. Liveness — the failure that looks like a decision

`Trader.step` read positions, computed targets, and journalled the result.
There was no state in which it could say "I could not tell."

Alpaca is REST: unreachable means an exception, which is loud. IBKR is a socket
to a TWS or IB Gateway process running on the same machine, and that process
can be **absent, logged out, or mid-restart** while everything else looks
normal. IBKR force-restarts the gateway once a day on a schedule, and the
socket drops after ~30 seconds of silence.

A trader that cannot detect this journals `no action — inside rebalance band`
on a day it was simply disconnected. Those two records are byte-identical and
mean opposite things, which destroys the reconciliation the journal exists for.

**Changed.** `Broker.is_ready()`, consulted by `Trader.step` before the
strategy is called at all. A broker without the method is assumed reachable —
it just answered a `positions()` call. `AlpacaPaperBroker.is_ready()` checks
account status and `trading_blocked`, catching the case the transport cannot:
the account replies normally and refuses every order. `SimulatedBroker` can be
switched offline so the behaviour is tested rather than hoped for.

### 3. Order identity — adapter-local, no protocol change

Alpaca: POST an order, get a server-assigned UUID back. IBKR: request a
monotonically increasing client-side `orderId` via `reqIds()`, place with it,
and receive fills asynchronously through callbacks. Reusing an id silently
modifies the previous order rather than placing a new one.

This is real work but it is entirely inside the adapter. `submit(order) -> str`
is satisfiable: allocate the id, place, return it as a string. No protocol
change needed.

### 4. Currency — `cash()` had no unit

An IBKR account is multi-currency. `cash()` returning "the cash" is ambiguous
in a way it never was at Alpaca. Documented as USD in the protocol; an adapter
must select the USD balance rather than the base-currency total.

---

## Keeping rule 4 intact under IBKR

`AlpacaPaperBroker` refuses to construct against a non-paper hostname. The
equivalent for IBKR is not one check but two, because either alone is bypassable:

- **Port.** Paper is 7497 (TWS) / 4002 (Gateway); live is 7496 / 4001. Refuse
  in the constructor, exactly as the Alpaca adapter refuses a live host.
- **Account id.** IBKR paper accounts are prefixed `DU`, live accounts `U`.
  Verify *after connecting* and disconnect if it does not match — a gateway
  configured for live can be reachable on a port someone assumed was paper.

The port check alone is not enough, and neither is the account check alone. Any
adapter that ships with only one of them has not honoured the rule.

---

## What was deliberately not done

**The adapter itself is not written.** It needs `ib_async` (the maintained
successor to `ib_insync`, whose author died in 2024) plus a running TWS or IB
Gateway, and neither exists in this environment. An untested socket adapter
against an asynchronous callback API is not groundwork — it is a file that
looks finished, which is worse than an absent one.

What is written is everything testable without an IBKR connection: the two
protocol additions, their implementations in `SimulatedBroker` and
`AlpacaPaperBroker`, and tests that a whole-share broker never receives a
fractional order and an unreachable broker blocks instead of looking idle.

**Order state is still not tracked.** `submit()` returns an id nobody ever asks
about again. Alpaca can reject or partially fill; IBKR certainly can. The
journal therefore records what the system *intended*, never what happened —
which is what it says it does, but it means reconciliation is a manual read of
two documents. A `status(order_id)` member would close this. It was left out
because it is a gap in the Alpaca path too, and fixing it belongs with the
work that actually reconciles, not here.

**None of this is blocked on code.** It is blocked on the same thing everything
else is: no strategy in this repository has passed validation, so there is
nothing worth executing at any broker.
