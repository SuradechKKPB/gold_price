"""Compute-only: recompute the sell-timing score from the price history that lives in
Supabase, plus a live top-up of the world price. Never touches GTA, so it runs fine on
GitHub Actions where GTA 403s datacenter IPs. This is the only job on the cron, so it also
owns event alerting and the sell plan's daily decision.

The score's price BASIS is the international (world) gold price in THB, not the Thai
association quote (see etl/intl.py for why). intl.topup_live fetches that from keyless
world feeds that answer from any IP, so this job is self-sufficient — no phone, no GTA.
The association price stays the realized/displayed number elsewhere (dashboard headline,
digest body, backtest realized price).

Housekeeping behaviours that make the pipeline honest:
  - AUTO-HEAL: if the score formula version changed since signals_daily was last written,
    rewrite the WHOLE history so the backtest sees a single formula epoch.
  - PLAN: after the score is written, etl.plan decides what the next session should do and
    publishes it (state `plan_today`), so the dashboard and the Worker's digest read one
    decision instead of each re-deriving it.
  - LINE: fires an EVENT ping only when the market enters its sell zone (or a plan step
    occurs, if broadcast), deduped via etl.state. The fixed-time daily digest belongs to the
    Cloudflare Worker (worker/), whose cron is precise to the second; keeping a second
    digest sender here would double-spend a LINE quota that is already the binding constraint.
"""

from __future__ import annotations

import argparse

from . import advice, alerts, indicators, intl, load, plan, signals, state
from . import dxy as dxy_mod
from .config import settings


def publish_trail_state(sb, ind, scores, days: int = 250) -> None:
    """Publish the brake state the score reads: distance to the 40-bar high and the band.

    Computed HERE, from the same indicators.build() and signals.brake_band() the score
    uses, rather than recomputed in the dashboard's TypeScript and the Worker's JavaScript.
    The lookback and the band would otherwise live in three places and drift. The v3 3%/8%
    band was hand-copied into the dashboard exactly like that. The band is now published,
    so nothing downstream copies a constant.
    """
    tail = ind.tail(days)
    load.upsert_macro(sb, "dd_from_high", tail["dd_from_high"], "etl_compute")
    load.upsert_macro(sb, "recent_high_40", tail["recent_high"], "etl_compute")
    load.upsert_macro(sb, "brake_dd", scores["brake_dd"].tail(days), "etl_compute")
    last = tail.iloc[-1]
    band = float(scores["brake_dd"].iloc[-1])
    print(
        f"trail state: {last['dd_from_high'] * 100:.1f}% below the 40-bar high "
        f"({last['recent_high']:,.0f}); brake band {band * 100:.1f}%."
    )


def score_and_publish(sb, *, force_full: bool = False, buy_in: float | None = None) -> dict:
    """Score the stored history, write it, run the plan and the alerts. Shared by the cron
    (main below) and the manual GTA ingest (etl.run), so the two cannot drift apart."""
    daily = intl.load_intl_daily(sb)         # world gold in THB (96.5% basis), weekday closes
    ind = indicators.build(daily, 0.0)       # no association bid/ask spread on the world price
    scores = signals.compute_scores(ind)
    publish_trail_state(sb, ind, scores)

    # AUTO-HEAL: a formula change (SCORE_VERSION bump) rewrites the full history so the
    # backtest never mixes vintages; otherwise only the recent tail needs refreshing.
    stored = state.get_score_version(sb)
    full = force_full or stored != signals.SCORE_VERSION
    n = signals.upsert_signals(sb, scores if full else scores.tail(30))
    pruned = 0
    if full:
        # Upsert alone cannot deliver a single-epoch history: it rewrites the dates the
        # current basis covers and silently leaves every other date on its old formula.
        pruned = signals.prune_signals(sb, scores)
        state.set_score_version(sb, signals.SCORE_VERSION)

    status = plan.today_status(sb, scores)
    if status is not None:
        plan.publish(sb, status)
        print(plan.describe(status))

    advice.topup_premium(sb)                 # refresh local-premium z for the dashboard
    extra = advice.advice_line(advice.build_advice(sb))
    alert_status = alerts.alert(sb, scores, status, buy_in=buy_in, extra=extra)

    latest = scores.iloc[-1]
    print(
        f"Recomputed {len(scores)} intl scores; wrote {n} rows "
        f"({'FULL backfill v' + str(signals.SCORE_VERSION) + f', pruned {pruned} stale' if full else 'tail-30'}). "
        f"Latest {latest.name.date()}: {latest['sell_pressure']:.0f}/100 -> {latest['verdict']} "
        f"({latest['active_signals']}). {alerts.status_line(alert_status)}"
    )
    return {"alert_status": alert_status, "latest": latest, "plan": status}


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
    dxy_mod.topup(sb)                        # context series for etl.dxy; not in the v4 score
    line_ok = alerts.check_channels()        # reads only — costs no LINE quota
    out = score_and_publish(sb, force_full=force_full)

    # An undelivered event is the one failure mode nobody can see from the inside: LINE
    # is the channel, so LINE cannot report its own silence. Fail the job instead — a red
    # run in the Actions list (and its notification mail) is the out-of-band signal. The
    # alert itself is not lost: state was not advanced, so the next run retries it.
    if out["alert_status"] == "failed":
        raise SystemExit("a sell-zone event could not be delivered to LINE")
    if not line_ok:
        raise SystemExit("no usable LINE channel — a sell-zone event could not be delivered")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="force a full-history rewrite of signals_daily")
    args = ap.parse_args()
    main(force_full=args.full)
