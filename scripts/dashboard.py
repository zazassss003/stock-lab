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
    symbols = [a for a in sys.argv[1:] if not a.startswith("-")] or None
    info = refresh_and_report(symbols, ROOT / "dashboard.html")

    size_kb = info["path"].stat().st_size / 1024
    print(f"wrote {info['path']} ({size_kb:,.0f} KB)")
    print(f"  {len(info['symbols'])} symbols, newest bar {info['age_days']}d old")

    for symbol in info["symbols"]:
        print(f"  {symbol:<7}{info['bars'][symbol]:>6} bars  through {info['last_bar'][symbol]}")

    if info["failures"]:
        print(f"  FAILED: {info['failures']}")
    if info["stale"]:
        print(f"  WARNING: stale symbols {info['stale_symbols']}")


if __name__ == "__main__":
    main()
