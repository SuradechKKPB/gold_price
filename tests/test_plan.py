import datetime as dt

import numpy as np
import pytest

from etl import plan
from etl.plan import Rules, decide, slot_ends

L = 64  # the live campaign: 2026-10-02 .. 2026-12-30 in weekdays
R = Rules()


def replay(verdicts, n_days=L, rules=R):
    """Run decide() day by day the way the backtest does; return the decision days that sold."""
    sold, last, days = 0, None, []
    for t in range(n_days - 1):
        d = decide(t, n_days, sold, last, verdicts[t], rules)
        if d.action == "sell":
            days.append(t)
            sold += 1
            last = t
    return days


def test_slot_ends_leave_room_for_the_last_t_plus_1_fill():
    ends = slot_ends(L, 5)
    assert len(ends) == 5
    assert list(ends) == sorted(set(ends))
    assert ends[-1] == L - 2


def test_neutral_tape_sells_exactly_at_slot_ends():
    assert replay(["neutral"] * L) == list(slot_ends(L, 5))


def test_very_rich_sells_early_but_never_two_tranches_in_one_slot():
    days = replay(["very_rich"] * L)
    ends = slot_ends(L, 5)
    assert days[0] == 0
    # each later sale is the first day of the next slot, never a second sale inside a slot
    assert days[1:] == [e + 1 for e in ends[:-1]]


def test_rich_below_the_trigger_does_not_pull_a_sale_forward():
    assert replay(["rich"] * L) == list(slot_ends(L, 5))


def test_weak_day_at_due_waits_then_sells_within_grace():
    ends = slot_ends(L, 5)
    v = ["neutral"] * L
    for t in range(ends[0], ends[0] + 4):
        v[t] = "weak"
    days = replay(v)
    assert days[0] == ends[0] + 4          # first non-weak day after the due date
    d = decide(ends[0], L, 0, None, "weak", R)
    assert (d.action, d.reason, d.wait_until_t) == ("wait", "brake", ends[0] + R.grace)


def test_grace_is_a_hard_limit():
    ends = slot_ends(L, 5)
    days = replay(["weak"] * L)
    assert days[0] == ends[0] + R.grace


@pytest.mark.parametrize("seed", range(25))
def test_every_tranche_is_sold_by_the_deadline_whatever_the_tape(seed):
    rng = np.random.default_rng(seed)
    v = list(rng.choice(["weak", "neutral", "rich", "very_rich"], size=L, p=[0.4, 0.3, 0.15, 0.15]))
    days = replay(v)
    assert len(days) == 5
    assert max(days) <= L - 2                # its T+1 fill is inside the window


def test_all_weak_still_completes_the_plan():
    assert len(replay(["weak"] * L)) == 5


def test_done_after_the_last_tranche():
    assert decide(10, L, 5, 3, "very_rich", R).action == "done"


def _spec():
    return plan.Spec(
        start=dt.date(2026, 10, 2), deadline=dt.date(2026, 12, 30), sell_grams=500.0, holding_grams=1000.0, rules=Rules()
    )


def test_live_calendar_and_schedule():
    spec = _spec()
    assert len(plan.calendar(spec)) == 64
    s = plan.evaluate(spec, [], dt.date(2026, 10, 1), "weak", 9.0)
    assert s["action"] == "hold" and s["tranche"] == 1
    assert s["next_grams"] == 100.0
    assert s["schedule"] == ["2026-10-21", "2026-11-06", "2026-11-25", "2026-12-11", "2026-12-30"]
    assert s["due_date"] == s["schedule"][0]


def test_partial_tranche_counts_grams_not_sales():
    s = plan.evaluate(_spec(), [{"date": "2026-10-05", "grams": 150, "price": 70000}], dt.date(2026, 10, 6), "neutral", 60.0)
    assert s["tranches_sold"] == 1
    assert s["remaining_grams"] == 350.0
    assert s["tranche"] == 2


def test_a_bar_dated_on_the_due_session_says_sell():
    # Bar D is written before session D opens, so it decides session D (decision t = idx(D)-1).
    assert plan.evaluate(_spec(), [], dt.date(2026, 10, 21), "neutral", 60.0)["action"] == "sell"
    assert plan.evaluate(_spec(), [], dt.date(2026, 10, 20), "neutral", 60.0)["action"] == "hold"


def test_cooldown_counts_the_same_sessions_live_and_in_the_backtest():
    # Tranche 1 sold in session 2026-10-19. Slot 2 opens with session 2026-10-22, but a
    # very_rich day there is only 3 sessions after the sale, inside the 5-session cooldown.
    sales = [{"date": "2026-10-19", "grams": 100, "price": 70000}]
    early = plan.evaluate(_spec(), sales, dt.date(2026, 10, 22), "very_rich", 95.0)
    assert (early["action"], early["reason"]) == ("hold", "ahead")
    later = plan.evaluate(_spec(), sales, dt.date(2026, 10, 26), "very_rich", 95.0)
    assert (later["action"], later["reason"], later["tranche"]) == ("sell", "rich", 2)


def test_behind_at_the_deadline_sells_everything_left():
    # Tranche 4 was missed: 300 g sold going into the last two sessions.
    sales = [{"date": d, "grams": 100, "price": 70000} for d in ("2026-10-21", "2026-11-06", "2026-11-25")]
    day1 = plan.evaluate(_spec(), sales, dt.date(2026, 12, 29), "weak", 20.0)
    assert (day1["action"], day1["reason"], day1["next_grams"]) == ("sell", "deadline", 100.0)
    last = plan.evaluate(_spec(), sales, dt.date(2026, 12, 30), "weak", 20.0)
    assert (last["action"], last["reason"], last["next_grams"]) == ("sell", "deadline", 200.0)


def test_decide_spreads_a_backlog_over_the_sessions_left():
    assert decide(L - 2, L, 3, None, "neutral", R).count == 2
    assert decide(L - 3, L, 1, None, "neutral", R).count == 2   # 4 outstanding, 2 decisions left


def test_past_deadline_reports_what_is_unsold():
    s = plan.evaluate(_spec(), [{"date": "2026-10-21", "grams": 100, "price": 70000}], dt.date(2026, 12, 31), "neutral", 50.0)
    assert (s["action"], s["remaining_grams"]) == ("ended", 400.0)
