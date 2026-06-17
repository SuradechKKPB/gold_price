"""Daily ETL orchestrator.

Phase 1 scope: capture the live GTA round and refresh the daily bar series.
Later phases add: macro/fundamental pulls, indicators, sell-pressure signals,
backtest refresh, and LINE alerts. Runs DB-less (prints) when Supabase env is absent.
"""

from __future__ import annotations

from .config import settings
from .gta import fetch_latest, fetch_ohlc, ohlc_to_daily


def main() -> None:
    tick = fetch_latest()
    daily = ohlc_to_daily(fetch_ohlc())
    headline = (tick.bar_buy or 0) * settings.baht_weight

    print(f"GTA round {tick.seq} @ {tick.as_time}")
    print(f"  bar buy-in {tick.bar_buy:,.0f} THB/baht-weight")
    print(f"  holding {settings.gold_grams:g} g = {settings.baht_weight:.2f} baht-weight"
          f"  ->  ~{headline:,.0f} THB if sold now")
    print(f"  daily series: {len(daily):,} days "
          f"({daily['trade_date'].min()} -> {daily['trade_date'].max()})")

    if not settings.has_supabase:
        print("\n[no Supabase env] dry run only — nothing written.")
        return

    from . import load

    sb = load.client()
    load.upsert_tick(sb, tick)
    n = load.upsert_daily(sb, daily, settings.bar_spread_thb)
    print(f"\nUpserted 1 tick and {n:,} daily rows to Supabase.")


if __name__ == "__main__":
    main()
