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
from . import dxy as dxy_mod  # aliased: `dxy` is the series variable in main()
from .config import settings


def publish_trail_state(sb, ind, days: int = 250) -> None:
    """Publish the trailing-stop state the score is actually reading.

    The composite CANNOT warn you at a high: trend_break carries 40% of the weight and is
    zero by construction until price is TRAIL_X below the recent high, so the arithmetic
    ceiling at a new high is ~39 against a trim line of 44. Measured over 2007-2026, not
    one of the 884 bars at a new high ever scored above 35.9. That is the design (a
    trailing stop fires after the turn), not a defect — but it means the score alone
    cannot tell you where you stand while the top is forming.

    So the distance to the recent high is published as its own number. It is computed HERE,
    from the same indicators.build() the score reads, rather than recomputed in the
    dashboard's TypeScript and the Worker's JavaScript — LB and the 3%/8% band would then
    live in three places and drift (see HANDOFF's note on the hand-copied DXY_TABLE).
    """
    tail = ind.tail(days)
    load.upsert_macro(sb, "dd_from_high", tail["dd_from_high"], "etl_compute")
    load.upsert_macro(sb, "recent_high_40", tail["recent_high"], "etl_compute")
    last = tail.iloc[-1]
    print(f"trail state: {last['dd_from_high'] * 100:.1f}% below the 40-bar high ({last['recent_high']:,.0f}).")


def main(force_full: bool = False) -> None:
    if not settings.has_supabase:
        print("No Supabase env; nothing to compute.")
        return
    sb = load.client()
    intl.topup_from_daily(sb)                # recent intl re-derived from stored spot/fx
    live = intl.topup_live(sb)               # SELF-SUFFICIENT: today's intl from keyless world feeds
    # None is routine, not an error: weekends and rejected suspect quotes both land here,
    # and the right response to both is to score the last real bar. topup_live says why.
    print(f"live intl today: {live:,.0f}" if live else "no live bar written — scoring the last stored close.")
    daily = intl.load_intl_daily(sb)         # world gold in THB (96.5% basis), daily OHLC
    ind = indicators.build(daily, 0.0)       # no association bid/ask spread on the world price
    publish_trail_state(sb, ind)             # what the score reads but can never say out loud
    dxy_mod.topup(sb)                        # keep the dollar sub-score off a stale ffill
    dxy = load.fetch_macro(sb, "dxy")
    scores = signals.compute_scores(ind, dxy)
    latest = scores.iloc[-1]

    # AUTO-HEAL: a formula change (SCORE_VERSION bump) rewrites the full history so the
    # backtest never mixes vintages; otherwise only the recent tail needs refreshing.
    stored = state.get_score_version(sb)
    full = force_full or stored != signals.SCORE_VERSION
    to_write = scores if full else scores.tail(30)
    n = signals.upsert_signals(sb, to_write)
    pruned = 0
    if full:
        # Upsert alone cannot deliver a single-epoch history: it rewrites the dates the
        # current basis covers and silently leaves every other date on its old formula.
        pruned = signals.prune_signals(sb, scores)
        state.set_score_version(sb, signals.SCORE_VERSION)

    advice.topup_premium(sb)                        # refresh local-premium z for the dashboard
    extra = advice.advice_line(advice.build_advice(sb))  # personal campaign overlay for the message
    sent = alerts.alert_on_transition(sb, scores, extra=extra)
    line = "LINE transition alert sent." if sent else "No alert."

    print(
        f"Recomputed {len(scores)} intl scores; wrote {n} rows "
        f"({'FULL backfill v' + str(signals.SCORE_VERSION) + f', pruned {pruned} stale' if full else 'tail-30'}). "
        f"Latest {latest.name.date()}: {latest['sell_pressure']:.0f}/100 -> {latest['verdict']} "
        f"({latest['active_signals']}). {line}"
    )


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="force a full-history rewrite of signals_daily")
    args = ap.parse_args()
    main(force_full=args.full)
