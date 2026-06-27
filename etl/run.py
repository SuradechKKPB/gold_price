"""Daily ETL orchestrator — safe by design.

Gets today's GTA tick + (optionally) the /ohlc history. GTA blocks datacenter IPs
(e.g. GitHub Actions) with 403; there is no reliable free fresh fallback, so if the
live price can't be fetched — or looks implausible vs the last stored close — the
run SKIPS cleanly (no DB write, no LINE alert) instead of corrupting data or firing
a false signal. History lives in Supabase; the nightly run only needs today's tick.
"""

from __future__ import annotations

import pandas as pd

from . import indicators, intl, signals
from .config import settings
from .gta import GoldTick, fetch_latest, fetch_ohlc, ohlc_to_daily

PLAUSIBLE_DEV = 0.12  # reject a tick deviating >12% from the last stored close


def _merge_today(daily: pd.DataFrame, tick: GoldTick) -> pd.DataFrame:
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


def _last_close(daily: pd.DataFrame, today) -> float | None:
    prev = daily[daily["trade_date"] < today]
    return float(prev.iloc[-1]["bar_sell_close"]) if len(prev) else None


def main() -> None:
    tick = None
    try:
        tick = fetch_latest()
    except Exception as exc:  # noqa: BLE001
        print(f"GTA /Latest unreachable: {type(exc).__name__} (likely 403 from this host)")

    daily = None
    try:
        daily = ohlc_to_daily(fetch_ohlc())
    except Exception as exc:  # noqa: BLE001
        print(f"GTA /ohlc unreachable: {type(exc).__name__}")

    if not settings.has_supabase:
        print(f"[dry] tick={'ok' if tick else 'none'} ohlc={'ok' if daily is not None else 'none'} — no Supabase env, nothing written.")
        return

    from . import alerts, load

    sb = load.client()
    if daily is None:
        daily = load.fetch_daily(sb)

    if tick is None or not tick.bar_sell:
        print("SKIPPED: no fresh price (GTA blocked from this host). No write, no alert.")
        return

    today = pd.to_datetime(tick.as_time).date()
    last = _last_close(daily, today)
    if last and abs(tick.bar_sell - last) / last > PLAUSIBLE_DEV:
        print(f"SKIPPED: tick {tick.bar_sell:,.0f} deviates >{PLAUSIBLE_DEV:.0%} from last close {last:,.0f} — likely bad data. No write, no alert.")
        return

    daily = _merge_today(daily, tick)

    # --- score basis = international (world gold in THB), not the association quote ---
    # persist today's intl from the tick's spot/fx, then score on the world-price series.
    if tick.gold_spot_usd and tick.baht_per_usd:
        intl.upsert_today(sb, today, tick.gold_spot_usd, tick.baht_per_usd)
    intl.topup_from_daily(sb)               # also catch any phone-written days
    daily_intl = intl.load_intl_daily(sb)
    ind = indicators.build(daily_intl, 0.0)
    dxy = load.fetch_macro(sb, "dxy")
    scores = signals.compute_scores(ind, dxy)
    latest = scores.iloc[-1]

    load.upsert_tick(sb, tick)
    n_daily = load.upsert_daily(sb, daily.tail(7), settings.bar_spread_thb)
    n_sig = signals.upsert_signals(sb, scores.tail(30))
    sent = alerts.maybe_alert(scores, tick)

    print(f"OK: buy-in {tick.bar_buy:,.0f}; sell-pressure {latest['sell_pressure']:.0f}/100 -> {latest['verdict']}")
    print(f"Upserted 1 tick, {n_daily} daily, {n_sig} signal rows. {'LINE alert sent.' if sent else 'No alert.'}")


if __name__ == "__main__":
    main()
