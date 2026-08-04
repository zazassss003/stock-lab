"""The tracked symbol list.

**Selection-bias warning — read before backtesting any of these.**

This list is "the most profitable US companies *as of 2026*". Choosing it today
and testing it against 2015-2026 is survivorship bias in its purest form: these
names were selected precisely *because* they won. A backtest over them will
look excellent and mean nothing, because the 2015 version of this list would
have contained different companies, some of which have since done badly.

So:

- **Tracking them forward is fine** — that is what a watchlist is.
- **Backtesting a strategy on them is not evidence.** Use SPY, or reconstruct
  historical index membership, if you want a result you can believe.

`lookahead-hunter` is written to flag exactly this; it is documented here so it
cannot be discovered as a surprise later.

Ranking sources: Fortune Global 500 (2026) for the top four, which each cleared
$100B — Alphabet, Nvidia, Apple, Microsoft. Positions 5-10 follow Visual
Capitalist's ranked net-income list (July 2025, TradingView data). Exact order
varies by source and by whether figures are fiscal-year or trailing-twelve-
month, so treat the ordering as approximate and the membership as the point.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Listing:
    symbol: str
    name: str
    sector: str


# Order is by reported net income, most profitable first.
TOP_PROFIT: list[Listing] = [
    Listing("GOOGL", "Alphabet", "Technology"),
    Listing("NVDA", "NVIDIA", "Semiconductors"),
    Listing("AAPL", "Apple", "Technology"),
    Listing("MSFT", "Microsoft", "Technology"),
    Listing("BRK-B", "Berkshire Hathaway", "Conglomerate"),
    Listing("META", "Meta Platforms", "Technology"),
    Listing("AMZN", "Amazon", "Consumer / Cloud"),
    Listing("JPM", "JPMorgan Chase", "Banking"),
    Listing("XOM", "Exxon Mobil", "Energy"),
    Listing("BAC", "Bank of America", "Banking"),
]

# The thing every single name above has to beat to be worth holding separately.
BENCHMARK = Listing("SPY", "S&P 500 ETF", "Benchmark")

DEFAULT_UNIVERSE: list[Listing] = [BENCHMARK, *TOP_PROFIT]

BY_SYMBOL: dict[str, Listing] = {listing.symbol: listing for listing in DEFAULT_UNIVERSE}

SYMBOLS: list[str] = [listing.symbol for listing in DEFAULT_UNIVERSE]


def name_of(symbol: str) -> str:
    listing = BY_SYMBOL.get(symbol)
    return listing.name if listing else symbol
