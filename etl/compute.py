"""Compute-only: recompute the sell-pressure signals from the price history that
already lives in Supabase. Touches ONLY Supabase — never GTA — so it runs fine on
GitHub Actions, where GTA's 403 blocks the live fetch. This is the ONLY job on the
6-hourly cron, so it also owns event alerting.

The score's price BASIS is the international (world) gold price in THB, not the Thai
association quote (see etl/intl.py for why). The phone writes goldSpot + bahtPerUSD on
every sync, so intl.topup_from_daily derives the freshest days with no external call,
keeping this cron self-sufficient. The association price stays the realized/displayed
number elsewhere (dashboard headline, backtest realized price).

Housekeeping behaviours that make the pipeline honest:
  - AUTO-HEAL: if the score formula version changed since signals_daily was last
    written, rewrite the WHOLE history so the backtest sees a single formula epoch.
  - LINE: --digest sends a fixed-time daily card (07:00 / 16:00 ICT runs, always);
    other runs only fire an EVENT ping on a verdict transition (dedup via etl.state).
"""

from __future__ import annotations

import argparse

from . import advice, alerts, indicators, intl, load, signals, state
from .config import settings


def main(force_full: bool = False, digest: bool = False) -> None:
    if not settings.has_supabase:
        print("No Supabase env; nothing to compute.")
        return
    sb = load.client()
    intl.topup_from_daily(sb)                # recent intl from phone-written spot/fx (if any)
    live = intl.topup_live(sb)               # SELF-SUFFICIENT: today's intl from keyless world feeds,
    print(f"live intl today: {live:,.0f}" if live else "live intl fetch failed (using phone data)")
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
    if digest:
        sent = alerts.send_daily_digest(sb, scores, extra=extra)
        line = "LINE daily digest sent." if sent else "digest NOT sent (no LINE token)."
    else:
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
    ap.add_argument("--digest", action="store_true", help="send the fixed-time daily digest LINE (07:00/16:00 runs)")
    args = ap.parse_args()
    main(force_full=args.full, digest=args.digest)
