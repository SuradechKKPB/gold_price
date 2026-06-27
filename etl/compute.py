"""Compute-only: recompute the sell-pressure signals from the price history that
already lives in Supabase. Touches ONLY Supabase — never GTA — so it runs fine on
GitHub Actions, where GTA's 403 blocks the live fetch.

The score's price BASIS is the international (world) gold price in THB, not the Thai
association quote (see etl/intl.py for why). The phone writes goldSpot + bahtPerUSD on
every sync, so intl.topup_from_daily derives the freshest days with no external call,
keeping this cron self-sufficient. The association price stays the realized/displayed
number elsewhere (dashboard headline, backtest realized price).
"""

from __future__ import annotations

from . import indicators, intl, load, signals
from .config import settings


def main() -> None:
    if not settings.has_supabase:
        print("No Supabase env; nothing to compute.")
        return
    sb = load.client()
    intl.topup_from_daily(sb)                # refresh recent intl from phone-written spot/fx
    daily = intl.load_intl_daily(sb)         # world gold in THB (96.5% basis), daily OHLC
    ind = indicators.build(daily, 0.0)       # no association bid/ask spread on the world price
    dxy = load.fetch_macro(sb, "dxy")
    scores = signals.compute_scores(ind, dxy)
    latest = scores.iloc[-1]
    n = signals.upsert_signals(sb, scores.tail(30))
    print(
        f"Recomputed {n} signal rows from {len(daily)} intl prices. "
        f"Latest {latest.name.date()}: {latest['sell_pressure']:.0f}/100 -> {latest['verdict']} "
        f"({latest['active_signals']})"
    )


if __name__ == "__main__":
    main()
