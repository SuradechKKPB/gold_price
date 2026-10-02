"""The sell plan: PACE (how much, by when) and the rule that picks the days to spend it on.

The 2026-10-02 audit split two questions the old ladder had fused into one score:

  - PACE is the seller's decision: sell `sell_grams` of the holding in `n_tranches` equal
    tranches by `deadline`. Over a campaign, pace moves the realized price far more than
    day choice does, because selling earlier or later in a trending market is a bet on the
    trend. The tool does not get to make that bet on the seller's behalf.
  - DAY CHOICE is the score's job (etl/signals.py). The plan cuts the window into equal
    slots, one tranche per slot. A tranche is sold on the first RICH day of its slot. If no
    rich day comes, it is sold at the end of the slot. If the slot ends on a WEAK day (the
    brake), the sale may wait up to `grace` trading days. The deadline is never moved:
    once the trading days left are no more than the tranches outstanding, it sells.

`decide()` is pure and works in trading-day indices, so etl/backtest.py replays exactly the
function the live plan runs. A decision made on day t's close is executed at the next
session (t+1). The schedule therefore ends one day before the deadline, so the last fill
still lands inside the window.

Everything personal lives here, never in signals_daily: that table is a market property
and stays identical whoever reads it. The ledger of sales actually made is kept in
etl.state under `plan_sales`.

CLI (needs .env with Supabase keys):
    python -m etl.plan                          # today's decision + progress
    python -m etl.plan sold 100 --price 71650   # record a sale (date defaults to today, BKK)
    python -m etl.plan undo                     # remove the last recorded sale
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from dataclasses import asdict, dataclass
from functools import lru_cache

import pandas as pd

from .config import settings

# Ordering of the market verdicts (etl/signals.py VERDICTS), weakest first.
_RANK = {"weak": 0, "neutral": 1, "rich": 2, "very_rich": 3}


@dataclass(frozen=True)
class Rules:
    """Defaults are the pre-2020 grid choice in etl/backtest.py (GRID); change them there."""

    n_tranches: int = 5
    trigger: str = "very_rich"  # lowest verdict that sells a tranche before its slot ends
    cooldown: int = 5           # trading days between two sales pulled forward by a rich day
    grace: int = 10             # trading days a due tranche may wait out a weak tape


@dataclass(frozen=True)
class Decision:
    action: str             # sell | wait | hold | done
    reason: str             # rich | due | deadline | brake | ahead | plan_met
    tranche: int            # 1-based tranche this decision concerns
    due_t: int | None       # index of the day that tranche falls due
    wait_until_t: int | None
    count: int = 1          # tranches to sell this session: >1 only when behind at the deadline


@lru_cache(maxsize=64)
def slot_ends(n_days: int, n: int) -> tuple[int, ...]:
    """Decision-day index at which each tranche falls due. The last is n_days-2 so its T+1
    fill is the final day of the window."""
    span = n_days - 1
    return tuple(max(0, round((k + 1) * span / n) - 1) for k in range(n))


def decide(t: int, n_days: int, sold: int, last_sale_t: int | None, verdict: str, rules: Rules) -> Decision:
    """What to do at the next session, given the close of decision day t."""
    n = rules.n_tranches
    if sold >= n:
        return Decision("done", "plan_met", n, None, None)

    ends = slot_ends(n_days, n)
    due_t = ends[sold]
    outstanding = n - sold
    decisions_left = n_days - 1 - t      # decision days from today to the last one, inclusive
    tranche = sold + 1

    if decisions_left <= outstanding:
        # Behind schedule near the end (a missed session): spread what is left over the
        # sessions left, and sell everything on the last one.
        return Decision("sell", "deadline", tranche, due_t, None, count=-(-outstanding // decisions_left))

    # One tranche per slot that has started: a rich day may sell the current slot's
    # tranche early, or catch up one that a brake delayed, but never a future slot's.
    unlocked = next((k + 1 for k, e in enumerate(ends) if e >= t), n)
    cooled = last_sale_t is None or t - last_sale_t >= rules.cooldown
    if sold < unlocked and cooled and _RANK.get(verdict, 1) >= _RANK[rules.trigger]:
        return Decision("sell", "rich", tranche, due_t, None)

    if t >= due_t:
        waited = t - due_t
        if verdict == "weak" and waited < rules.grace and decisions_left > outstanding + 1:
            return Decision("wait", "brake", tranche, due_t, due_t + rules.grace)
        return Decision("sell", "due", tranche, due_t, None)

    return Decision("hold", "ahead", tranche, due_t, None)


# --- live plan -------------------------------------------------------------------------


@dataclass(frozen=True)
class Spec:
    start: dt.date
    deadline: dt.date
    sell_grams: float
    holding_grams: float
    rules: Rules

    @property
    def tranche_grams(self) -> float:
        return self.sell_grams / self.rules.n_tranches


def spec_from_settings() -> Spec | None:
    """The configured campaign, or None when no plan is set."""
    if not (settings.plan_start and settings.plan_deadline and settings.plan_sell_grams > 0):
        return None
    return Spec(
        start=dt.date.fromisoformat(settings.plan_start),
        deadline=dt.date.fromisoformat(settings.plan_deadline),
        sell_grams=settings.plan_sell_grams,
        holding_grams=settings.gold_grams,
        rules=Rules(n_tranches=settings.plan_tranches),
    )


def calendar(spec: Spec) -> pd.DatetimeIndex:
    """Trading days of the campaign: weekdays. Thai holidays are not modelled, so a due
    session can land on one (13 Oct, 23 Oct, 7 Dec, 10 Dec in 2026). If the shop is shut,
    sell the session before. The deadline itself is set to 30 Dec so the last tranche never
    depends on 31 Dec, which is a holiday."""
    return pd.bdate_range(spec.start, spec.deadline)


def _ledger(sb) -> list[dict]:
    from . import state

    s = state.get_state(sb, "plan_sales")
    if not s or not s.get("text"):
        return []
    return list(json.loads(s["text"]))


def _save_ledger(sb, sales: list[dict]) -> None:
    from . import state

    total = sum(float(x["grams"]) for x in sales)
    state.set_state(sb, "plan_sales", value=total, text=json.dumps(sales, ensure_ascii=False))


def evaluate(spec: Spec, sales: list[dict], bar_date: dt.date, verdict: str, rich: float | None) -> dict:
    """Decision for the session on `bar_date`, from the latest scored bar. Pure: no I/O.

    TIMING. A live bar dated D is written before the Thai session of D opens. The 01:00
    and 05:30 ICT runs stamp the world price with Bangkok date D, so bar D holds the close
    of the world session that ended just before D. It therefore plays the role of decision
    day t = idx(D) - 1, whose T+1 session is D itself. A sale recorded on date S maps the
    same way, to the decision made for session S. With both on one clock, the cooldown
    counts the same sessions here as in etl/backtest.py.
    """
    cal = calendar(spec)
    n_days = len(cal)
    sold_g = sum(float(x["grams"]) for x in sales)
    tranche_g = spec.tranche_grams
    sold_n = int(sold_g / tranche_g + 1e-9)
    remaining = max(spec.sell_grams - sold_g, 0.0)
    base = {
        "as_of": bar_date.isoformat(),
        "verdict": verdict,
        "rich": None if rich is None else round(float(rich), 1),
        "sold_grams": round(sold_g, 2),
        "sell_grams": spec.sell_grams,
        "holding_grams": spec.holding_grams,
        "n_tranches": spec.rules.n_tranches,
        "tranches_sold": min(sold_n, spec.rules.n_tranches),
        "start": spec.start.isoformat(),
        "deadline": spec.deadline.isoformat(),
        "remaining_grams": round(remaining, 2),
    }
    if n_days < 2:
        return {**base, "action": "ended", "reason": "window_too_short"}
    if pd.Timestamp(bar_date) > cal[-1]:
        return {**base, "action": "ended", "reason": "past_deadline"}

    def decision_t(d: dt.date | str) -> int:
        """Index of the decision whose session is on (or next after) date d."""
        return int(cal.searchsorted(pd.Timestamp(d))) - 1

    t = min(max(decision_t(bar_date), 0), n_days - 2)
    sale_ts = [decision_t(x["date"]) for x in sales]
    last_sale_t = max(sale_ts) if sale_ts else None

    d = decide(t, n_days, sold_n, last_sale_t, verdict, spec.rules)
    day = lambda i: None if i is None else cal[min(i + 1, n_days - 1)].date().isoformat()  # noqa: E731
    return {
        **base,
        **asdict(d),
        "due_date": day(d.due_t),            # the session the tranche is due to be sold in
        "wait_until": day(d.wait_until_t),   # the session it is sold in at the latest
        "next_grams": round(min(d.count * tranche_g, remaining), 2),   # all of it on the last session
        "schedule": [day(e) for e in slot_ends(n_days, spec.rules.n_tranches)],
    }


def publish(sb, status: dict) -> None:
    """Store today's plan decision for the dashboard and the digest (both read, never compute).

    `broadcast` travels with it so the Worker obeys the one switch in etl/config.py instead
    of keeping a second copy of it in wrangler.toml."""
    from . import state

    doc = {**status, "broadcast": settings.plan_in_broadcast}
    state.set_state(sb, "plan_today", value=float(status.get("tranches_sold", 0)), text=json.dumps(doc, ensure_ascii=False))


def today_status(sb, scores: pd.DataFrame | None = None) -> dict | None:
    """Decision for the latest scored bar, or None when no plan is configured.

    The cron passes the scores it just computed. The CLI passes nothing and reads the
    latest signals_daily row, which is the same bar once the cron has written it."""
    spec = spec_from_settings()
    if spec is None:
        return None
    if scores is not None and len(scores):
        last = scores.iloc[-1]
        bar, verdict, rich = scores.index[-1].date(), str(last["verdict"]), float(last["sell_pressure"])
    else:
        row = (
            sb.table("signals_daily").select("trade_date,sell_pressure,verdict").order("trade_date", desc=True).limit(1).execute().data
        )
        if not row:
            return None
        bar, verdict, rich = dt.date.fromisoformat(row[0]["trade_date"]), row[0]["verdict"], row[0]["sell_pressure"]
    return evaluate(spec, _ledger(sb), bar, verdict, rich)


_ACTION_TH = {"sell": "ขายไม้นี้ได้", "wait": "รอก่อน (เบรก)", "hold": "ถือรอ", "done": "ครบตามแผนแล้ว", "ended": "จบแผนแล้ว"}
_REASON_TH = {
    "rich": "วันนี้ราคาอยู่โซนแพง",
    "due": "ครบกำหนดไม้นี้แล้ว",
    "deadline": "ใกล้เส้นตาย ต้องขายตามแผน",
    "brake": "ครบกำหนดแต่ราคาอ่อนตัว",
    "ahead": "ยังไม่ถึงกำหนด ขายก่อนได้ถ้าเข้าโซนแพงมาก",
    "plan_met": "ขายครบตามเป้าแล้ว",
}


def describe(s: dict) -> str:
    """Human summary of a status dict (CLI and logs)."""
    head = f"plan {s['start']} → {s['deadline']}: sold {s['sold_grams']:g} / {s['sell_grams']:g} g ({s['tranches_sold']}/{s['n_tranches']} tranches)"
    act = s.get("action")
    if act in ("done", "ended"):
        return f"{head}\n  {act}: {_REASON_TH.get(s.get('reason', ''), s.get('reason', ''))}"
    line = f"  next: tranche {s['tranche']} ({s['next_grams']:g} g) due {s['due_date']} → {act} ({s['reason']}: {_REASON_TH.get(s['reason'], '')})"
    if s.get("wait_until"):
        line += f", wait no later than {s['wait_until']}"
    return f"{head}\n  market {s['as_of']}: {s['verdict']} (rich {s['rich']})\n{line}\n  schedule: {', '.join(s['schedule'])}"


def main() -> None:
    from . import intl, load

    ap = argparse.ArgumentParser(prog="python -m etl.plan")
    sub = ap.add_subparsers(dest="cmd")
    p_sold = sub.add_parser("sold", help="record a sale")
    p_sold.add_argument("grams", type=float)
    p_sold.add_argument("--price", type=float, required=True, help="association bid you sold at, THB per baht-weight")
    p_sold.add_argument("--date", default=None, help="ISO date of the sale (default: today, Bangkok)")
    sub.add_parser("undo", help="remove the most recently recorded sale (not the latest-dated one)")
    args = ap.parse_args()

    sb = load.client()
    if args.cmd == "sold":
        if args.grams <= 0 or args.price <= 0:
            raise SystemExit("grams and price must be positive")
        when = args.date or intl.bkk_today().date().isoformat()
        dt.date.fromisoformat(when)  # reject a malformed date before it is stored
        # Kept in the order recorded, not by date, so `undo` removes what was just typed
        # even when it was a back-dated entry. Nothing downstream depends on the order.
        sales = _ledger(sb) + [{"date": when, "grams": args.grams, "price": args.price}]
        _save_ledger(sb, sales)
        print(f"recorded: {args.grams:g} g at {args.price:,.0f} on {when}")
    elif args.cmd == "undo":
        sales = _ledger(sb)
        if not sales:
            raise SystemExit("ledger is empty")
        gone = sales.pop()
        _save_ledger(sb, sales)
        print(f"removed: {gone}")

    status = today_status(sb)
    if status is None:
        print("no plan configured (PLAN_START / PLAN_DEADLINE / PLAN_SELL_GRAMS)")
        return
    if args.cmd in ("sold", "undo"):
        publish(sb, status)   # the dashboard and digest reflect the sale without waiting for the cron
    for x in sorted(_ledger(sb), key=lambda x: x["date"]):
        print(f"  sale {x['date']}: {float(x['grams']):g} g @ {float(x['price']):,.0f}")
    print(describe(status))


if __name__ == "__main__":
    main()
