"""Daily ETL orchestrator.

Phase 1-2: capture the live GTA round, refresh the daily bar series, compute the
technical indicators and the 0-100 sell-pressure score. Later phases add
fundamentals, the backtest refresh, and LINE alerts. Runs DB-less (prints) when
Supabase env is absent.
"""

from __future__ import annotations

from . import indicators, signals
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

    ind = indicators.build(daily, settings.bar_spread_thb)
    scores = signals.compute_scores(ind)
    latest = scores.iloc[-1]
    print("\nSell-pressure score (latest):")
    print(f"  composite     {latest['sell_pressure']:.0f}/100   ->  verdict: {latest['verdict'].upper()}")
    print(f"  trend break   {latest['trend_break']:.0f}   overbought {latest['overbought']:.0f}"
          f"   momentum {latest['momentum']:.0f}   seasonality {latest['seasonality']:.0f}")
    print(f"  active        {latest['active_signals'] or '(none)'}")

    if not settings.has_supabase:
        print("\n[no Supabase env] dry run only — nothing written.")
        return

    from . import load

    sb = load.client()
    load.upsert_tick(sb, tick)
    n_daily = load.upsert_daily(sb, daily, settings.bar_spread_thb)
    n_sig = signals.upsert_signals(sb, scores)
    print(f"\nUpserted 1 tick, {n_daily:,} daily rows, {n_sig:,} signal rows to Supabase.")


if __name__ == "__main__":
    main()
