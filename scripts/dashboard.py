"""Generate the standalone HTML dashboard from the latest available data.

    py -3 scripts/dashboard.py [SYMBOL]

For the unattended daily run use scripts/update.py, which wraps this with
logging and a staleness exit code.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stocklab.pipeline import refresh_and_report  # noqa: E402


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    info = refresh_and_report(symbol, ROOT / "dashboard.html")

    size_kb = info["path"].stat().st_size / 1024
    print(f"wrote {info['path']} ({size_kb:,.0f} KB)")
    print(f"  {info['bars']} bars, {info['first_bar']} to {info['last_bar']} ({info['age_days']}d old)")
    if info["stale"]:
        print(f"  WARNING: newest bar is {info['age_days']} days old")

    for _, (label, result) in info["results"].items():
        stats = result.stats
        print(f"  {label:<12} return {stats['total_return']:>8.1%}   sharpe {stats['sharpe']:>5.2f}")


if __name__ == "__main__":
    main()
