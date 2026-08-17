"""What a trade actually costs — the four layers, not just the commission.

A backtest that charges one flat basis-point fee is modelling an advertisement,
not a broker. The real bill has four layers and they behave differently:

1. **Commission.** Zero at some brokers, per-share with a floor and a cap at
   others, a percentage of notional at Taiwanese sub-brokerage. The floor is
   what makes "$0.005 per share" expensive: a 10-share order still pays $1.
2. **Regulatory fees.** SEC Section 31 and FINRA's Trading Activity Fee. Tiny,
   charged on *sells only*, and identical at every US broker — nobody competes
   on them because nobody sets them. Modelled because "always" is easier to
   defend than "usually negligible".
3. **Currency.** For an account funded from Taiwan this is frequently the
   largest single line, and it is invisible in every broker comparison table
   because it happens at a bank. A 0.3% retail FX spread on a $20,000 transfer
   is $60 — more than a year of commissions for most people. It is charged when
   money crosses the currency line, not when a trade happens, so it is modelled
   separately from the per-trade layers.
4. **Slippage.** The gap between the price you decided on and the price you
   got. Already the dominant cost for anything traded often, and the one that
   grows fastest as size grows.

Rates below carry their effective date. They change; a number without a date is
a number nobody can check.

Nothing here is a recommendation of a broker. These are published fee schedules
turned into arithmetic so a backtest can stop pretending they are zero.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

# --- Statutory rates, identical at every US broker -------------------------
#
# SEC Section 31 fee: charged to the seller, expressed per million dollars of
# proceeds. The SEC re-sets it annually and mid-year; this is the rate that
# took effect 2026-04-04. At $20.60/million a $10,000 sale costs $0.21.
SEC_FEE_PER_MILLION = 20.60

# FINRA Trading Activity Fee: per share sold, capped per trade. Schedule A,
# Section 1 of the FINRA By-Laws. Sells only, like the Section 31 fee.
TAF_PER_SHARE = 0.000166
TAF_MAX_PER_TRADE = 8.30

BPS = 10_000.0


@dataclass(frozen=True)
class CostModel:
    """One broker's published schedule, as arithmetic.

    Every field is a cost, so every field is non-negative and larger is worse.
    `name` is mandatory and appears in reports: a result whose cost assumptions
    cannot be named is a result nobody can argue with.
    """

    name: str

    # Layer 1 — commission. A broker uses some of these, never all.
    commission_per_share: float = 0.0
    commission_bps: float = 0.0
    commission_min: float = 0.0
    # Cap as a fraction of notional, applied *after* the floor. This ordering is
    # not cosmetic: IBKR's $1 minimum would otherwise charge $1 on a $40 order
    # that its own 1% cap limits to $0.40.
    commission_max_bps: float = BPS  # 10,000bps = 100% = effectively uncapped

    # Layer 2 — statutory. Off only for a venue where they genuinely do not
    # apply; leaving them on costs ~0.2bp of sell notional and is never wrong.
    regulatory_fees: bool = True

    # Layer 3 — currency, charged when money enters or leaves the account.
    fx_spread_bps: float = 0.0
    fx_fixed: float = 0.0

    # Layer 4 — slippage, applied to the fill price rather than billed.
    slippage_bps: float = 5.0

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("a cost model must be named — reports print it")
        negatives = [
            field
            for field in (
                "commission_per_share",
                "commission_bps",
                "commission_min",
                "commission_max_bps",
                "fx_spread_bps",
                "fx_fixed",
                "slippage_bps",
            )
            if getattr(self, field) < 0
        ]
        if negatives:
            raise ValueError(f"costs cannot be negative: {negatives}")

    # -- per trade ----------------------------------------------------------

    def commission(self, shares: float, price: float) -> float:
        """Broker commission on one fill. `shares` may be signed."""
        quantity = abs(shares)
        notional = quantity * abs(price)
        if quantity == 0 or notional == 0:
            return 0.0

        charge = self.commission_per_share * quantity + self.commission_bps / BPS * notional
        charge = max(charge, self.commission_min)
        return min(charge, self.commission_max_bps / BPS * notional)

    def regulatory(self, shares: float, price: float) -> float:
        """SEC + FINRA fees. Sells only — a buy is free of both."""
        if not self.regulatory_fees or shares >= 0:
            return 0.0

        quantity = abs(shares)
        proceeds = quantity * abs(price)
        sec = proceeds * SEC_FEE_PER_MILLION / 1_000_000.0
        taf = min(quantity * TAF_PER_SHARE, TAF_MAX_PER_TRADE)
        return sec + taf

    def trade_cost(self, shares: float, price: float) -> tuple[float, float]:
        """`(commission, regulatory)` for one fill. Sign of `shares` matters."""
        return self.commission(shares, price), self.regulatory(shares, price)

    def fill_price(self, reference_price: float, shares: float) -> float:
        """Slippage always works against us: pay up to buy, sell into the bid."""
        direction = 1.0 if shares > 0 else -1.0
        return reference_price * (1.0 + direction * self.slippage_bps / BPS)

    # -- per money movement -------------------------------------------------

    def fx_cost(self, amount: float) -> float:
        """Cost of moving `amount` across the currency line, once.

        Charged on the way in *and* on the way out, so a round trip pays it
        twice. Zero for a model that ignores currency — which is the right
        setting when comparing strategies, and the wrong one when asking what
        the account actually returned.
        """
        if amount <= 0:
            return 0.0
        return self.fx_fixed + abs(amount) * self.fx_spread_bps / BPS

    # -- derived ------------------------------------------------------------

    @property
    def is_free(self) -> bool:
        """True when this model charges nothing. See `ZERO_COST_FOR_DEBUGGING`."""
        return (
            self.commission_per_share == 0
            and self.commission_bps == 0
            and self.commission_min == 0
            and self.slippage_bps == 0
            and not self.regulatory_fees
        )

    def min_cash_buffer(self) -> float:
        """Fraction of equity to hold back so a full allocation still clears.

        Costs come out of cash, so sizing to exactly 100% of equity ends the
        cycle overdrawn — harmless in a backtest, an insufficient-buying-power
        rejection at a real broker. This is the one-way cost, doubled for
        headroom. It cannot see the per-order commission floor, which depends
        on order size; `sizing.DEFAULT_CASH_BUFFER` is the floor under it.
        """
        one_way = (self.slippage_bps + self.commission_bps) / BPS
        return 2.0 * one_way

    def describe(self) -> str:
        """One line for a report footer. Says what was charged, not who charges it."""
        parts = []
        if self.commission_per_share:
            parts.append(f"${self.commission_per_share:g}/share")
        if self.commission_bps:
            parts.append(f"{self.commission_bps:g}bp commission")
        if self.commission_min:
            parts.append(f"min ${self.commission_min:g}")
        if not parts:
            parts.append("no commission")
        if self.regulatory_fees:
            parts.append("SEC/FINRA fees")
        parts.append(f"{self.slippage_bps:g}bp slippage")
        if self.fx_spread_bps or self.fx_fixed:
            fx = f"{self.fx_spread_bps:g}bp FX"
            if self.fx_fixed:
                fx += f" + ${self.fx_fixed:g}/transfer"
            parts.append(fx)
        return f"{self.name}: " + ", ".join(parts)

    def without_fx(self) -> "CostModel":
        """The same schedule with currency removed.

        Comparing two strategies inside one account should not charge either of
        them for a wire that happens once regardless of what they do.
        """
        return replace(self, name=f"{self.name} (ex-FX)", fx_spread_bps=0.0, fx_fixed=0.0)


# --- Presets ---------------------------------------------------------------
#
# Published schedules as of 2026-08. Verify before relying on any of them: fee
# schedules change more often than this file does.

#: What the engine charged before cost models existed. Kept as the default so
#: no historical result in this repository silently moves, and so a comparison
#: between strategies is not dominated by one broker's minimum ticket.
FLAT_DEFAULT = CostModel(
    name="broker-neutral",
    commission_bps=1.0,
    slippage_bps=5.0,
)

#: IBKR Pro, fixed tier. Non-US residents cannot use IBKR Lite, so the $1
#: minimum applies. FX is IDEALPRO: ~0.2bp plus a $2 minimum per conversion —
#: roughly two orders of magnitude below a retail bank spread, which is the
#: whole reason this preset exists.
IBKR_PRO = CostModel(
    name="IBKR Pro fixed",
    commission_per_share=0.005,
    commission_min=1.0,
    commission_max_bps=100.0,  # 1% of trade value
    fx_spread_bps=0.5,
    fx_fixed=2.0,
    slippage_bps=5.0,
)

#: Firstrade: no commission, but the account is funded by bank wire and the
#: currency conversion happens at a Taiwanese bank's retail rate. `fx_fixed`
#: bundles the outgoing wire, the intermediary bank's cut and the receiving
#: fee; `fx_spread_bps` is the retail spread.
FIRSTRADE = CostModel(
    name="Firstrade",
    fx_spread_bps=30.0,
    fx_fixed=45.0,
    slippage_bps=5.0,
)

#: Alpaca: commission-free, and the funding path depends on the country, so
#: currency is left at zero rather than guessed. That makes this preset an
#: understatement of the true cost from Taiwan, deliberately and visibly.
ALPACA = CostModel(name="Alpaca", slippage_bps=5.0)

#: Taiwanese sub-brokerage at the competitive end of the market (0.1%, no
#: minimum). No wire is needed, but the broker's own FX rate is a retail one.
SUB_BROKERAGE_TW = CostModel(
    name="sub-brokerage 0.1%",
    commission_bps=10.0,
    fx_spread_bps=20.0,
    slippage_bps=5.0,
)

#: Charges nothing. Exists to isolate a bug by subtraction, never to report a
#: result — a zero-cost backtest is not a good result, it is a broken one. The
#: name is long and unpleasant on purpose: it should be obvious in a diff.
ZERO_COST_FOR_DEBUGGING = CostModel(
    name="ZERO COST — debugging only, not a result",
    regulatory_fees=False,
    slippage_bps=0.0,
)

PRESETS: dict[str, CostModel] = {
    "flat": FLAT_DEFAULT,
    "ibkr": IBKR_PRO,
    "firstrade": FIRSTRADE,
    "alpaca": ALPACA,
    "subbrokerage": SUB_BROKERAGE_TW,
}
