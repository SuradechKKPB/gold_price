"""Compute-only: recompute the sell-pressure signals from the price history that
already lives in Supabase (written daily by the phone). Touches ONLY Supabase —
never GTA — so it runs fine on GitHub Actions, where GTA's 403 blocks the live
fetch. This keeps the dashboard score fresh: the phone writes the daily price,
this recomputes the score from it.
"""

from __future__ import annotations

from . import indicators, load, signals
from .config import settings


def main() -> None:
    if not settings.has_supabase:
        print("No Supabase env; nothing to compute.")
        return
    sb = load.client()
    daily = load.fetch_daily(sb)
    ind = indicators.build(daily, settings.bar_spread_thb)
    dxy = load.fetch_macro(sb, "dxy")
    scores = signals.compute_scores(ind, dxy)
    latest = scores.iloc[-1]
    n = signals.upsert_signals(sb, scores.tail(30))
    print(
        f"Recomputed {n} signal rows from {len(daily)} daily prices. "
        f"Latest {latest.name.date()}: {latest['sell_pressure']:.0f}/100 -> {latest['verdict']} "
        f"({latest['active_signals']})"
    )


if __name__ == "__main__":
    main()
