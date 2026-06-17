"""Daily ETL orchestrator (resilient to GTA blocking datacenter IPs).

Flow: get today's live tick (GTA -> thaigold fallback). For the daily series, use
the full GTA /ohlc when reachable; otherwise load history from Supabase and append
today's row from the tick. Then compute indicators + the sell-pressure score,
upsert the recent tail, and fire a LINE alert if the score crosses the threshold.
"""

from __future__ import annotations

import pandas as pd

from . import indicators, signals
from .config import settings
from .gta import GoldTick, fetch_ohlc, get_live_tick, ohlc_to_daily


def _merge_today(daily: pd.DataFrame, tick: GoldTick) -> pd.DataFrame:
    """Ensure today's row reflects the latest tick (sell-out basis)."""
    if not tick.bar_sell:
        return daily
    d = pd.to_datetime(tick.as_time).date()
    close = float(tick.bar_sell)
    mask = daily["trade_date"] == d
    if mask.any():
        i = daily.index[mask][0]
        daily.loc[i, "bar_sell_close"] = close
        daily.loc[i, "bar_sell_high"] = max(float(daily.loc[i, "bar_sell_high"]), close)
        daily.loc[i, "bar_sell_low"] = min(float(daily.loc[i, "bar_sell_low"]), close)
    else:
        row = {"trade_date": d, "bar_sell_open": close, "bar_sell_high": close, "bar_sell_low": close, "bar_sell_close": close}
        daily = pd.concat([daily, pd.DataFrame([row])], ignore_index=True)
    return daily.sort_values("trade_date").reset_index(drop=True)


def main() -> None:
    tick = get_live_tick()

    daily = None
    try:
        daily = ohlc_to_daily(fetch_ohlc())
        source = "GTA /ohlc (full)"
    except Exception as exc:  # noqa: BLE001
        source = f"GTA /ohlc unavailable ({type(exc).__name__})"

    print(f"Tick: {tick.as_time} (round {tick.seq}) bar buy-in {tick.bar_buy:,.0f} · source: {source}")

    if not settings.has_supabase:
        if daily is not None:
            ind = indicators.build(_merge_today(daily, tick), settings.bar_spread_thb)
            latest = signals.compute_scores(ind).iloc[-1]
            print(f"[dry] sell-pressure {latest['sell_pressure']:.0f}/100 -> {latest['verdict']}")
        print("[no Supabase env] dry run only — nothing written.")
        return

    from . import alerts, load

    sb = load.client()
    if daily is None:
        daily = load.fetch_daily(sb)
        source += " -> Supabase history"
    daily = _merge_today(daily, tick)

    ind = indicators.build(daily, settings.bar_spread_thb)
    scores = signals.compute_scores(ind)
    latest = scores.iloc[-1]

    load.upsert_tick(sb, tick)
    n_daily = load.upsert_daily(sb, daily.tail(7), settings.bar_spread_thb)
    n_sig = signals.upsert_signals(sb, scores.tail(30))
    sent = alerts.maybe_alert(scores, tick)

    print(f"source: {source}")
    print(f"sell-pressure {latest['sell_pressure']:.0f}/100 -> {latest['verdict']}  ({latest['active_signals']})")
    print(f"Upserted 1 tick, {n_daily} daily rows (tail), {n_sig} signal rows (tail).")
    print("LINE alert sent." if sent else "No LINE alert (no threshold cross or token unset).")


if __name__ == "__main__":
    main()
