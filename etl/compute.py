"""Compute-only: recompute the sell-pressure signals from the price history that lives in
Supabase, plus a live top-up of the world price. Never touches GTA, so it runs fine on
GitHub Actions where GTA 403s datacenter IPs. This is the only job on the cron, so it also
owns event alerting.

The score's price BASIS is the international (world) gold price in THB, not the Thai
association quote (see etl/intl.py for why). intl.topup_live fetches that from keyless
world feeds that answer from any IP, so this job is self-sufficient — no phone, no GTA.
The association price stays the realized/displayed number elsewhere (dashboard headline,
digest body, backtest realized price).

Housekeeping behaviours that make the pipeline honest:
  - AUTO-HEAL: if the score formula version changed since signals_daily was last written,
    rewrite the WHOLE history so the backtest sees a single formula epoch.
  - LINE: fires an EVENT ping only on a verdict transition, deduped via etl.state. The
    fixed-time daily digest belongs to the Cloudflare Worker (worker/), whose cron is
    precise to the second; keeping a second digest sender here would double-spend a LINE
    quota that is already the binding constraint.
"""

from __future__ import annotations

import argparse

from . import advice, alerts, indicators, intl, load, signals, state
from .config import settings


def main(force_full: bool = False) -> None:
    if not settings.has_supabase:
        print("No Supabase env; nothing to compute.")
        return
    sb = load.client()
    intl.topup_from_daily(sb)                # recent intl re-derived from stored spot/fx
    live = intl.topup_live(sb)               # SELF-SUFFICIENT: today's intl from keyless world feeds
    print(f"live intl today: {live:,.0f}" if live else "live intl fetch failed (using stored data)")
    daily = intl.load_intl_daily(sb)         # world gold in THB (96.5% basis), daily OHLC
    ind = indicators.build(daily, 0.0)       # no association bid/ask spread on the world price
    dxy = load.fetch_macro(sb, "dxy")
    scores = signals.compute_scores(ind, dxy)
    latest = scores.iloc[-1]

    # AUTO-HEAL: a formula change (SCORE_VERSION bump) rewrites the full history so the
    # backtest never mixes vintages; otherwise only the recent tail needs refreshing.
    stored = state.get_score_version(sb)
    full = force_full or stored != signals.SCORE_VERSION
    to_write = scores if full else scores.tail(30)
    n = signals.upsert_signals(sb, to_write)
    if full:
        state.set_score_version(sb, signals.SCORE_VERSION)

    advice.topup_premium(sb)                        # refresh local-premium z for the dashboard
    extra = advice.advice_line(advice.build_advice(sb))  # personal campaign overlay for the message
    sent = alerts.alert_on_transition(sb, scores, extra=extra)
    line = "LINE transition alert sent." if sent else "No alert."

    print(
        f"Recomputed {len(scores)} intl scores; wrote {n} rows "
        f"({'FULL backfill v' + str(signals.SCORE_VERSION) if full else 'tail-30'}). "
        f"Latest {latest.name.date()}: {latest['sell_pressure']:.0f}/100 -> {latest['verdict']} "
        f"({latest['active_signals']}). {line}"
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="force a full-history rewrite of signals_daily")
    args = ap.parse_args()
    main(force_full=args.full)
