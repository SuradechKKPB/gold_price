"""Backtest harness: what a seller actually gets, with drift kept apart from skill.

The pre-v4 harness compared a laddered score against DCA-out and called the gap an edge.
The 2026-10-02 audit showed most of that gap was TIMING, not skill. The ladder rarely fired
in a bull market, so it held to the window end and collected the trend (corr 0.65 with
"sell everything at the end"). In the 2011-18 bear the same habit lost. This harness asks
two questions separately, and every number it prints says which one it answers:

  1. DAY SKILL, drift-neutral. On the days a rule actually sells, how does the T+1 fill
     compare with the centred ±63-day average price around it? A centred window cancels
     the trend, so a positive number means the rule sold above the prices around it.
     Nothing else in this file can tell skill from luck in a trending market.
  2. CAMPAIGN. The deployed plan (etl.plan.decide, the same function the live plan runs)
     is replayed over rolling campaigns shaped like the live one. It is compared with two
     benchmarks that sell the same tranches in the same slots: at the slot END (the plan
     with no signal) and at the slot MIDDLE (≈ a random day in the slot, i.e. no skill).
     The average sale day is printed beside every edge. A rule that "wins" only by selling
     later in a bull market shows up as a later sale day, not as skill.

Every figure is split by regime (2006-11 bull, 2011-18 bear, 2019-26 bull). A rule passes
only if its day skill is positive in all three, and if it beats the random-day benchmark in
all three. Parameters are chosen on campaigns that START before 2020 and reported on the
rest. Signal basis = international THB close. Realized price = association bid (what Poom
sells into); its deep history is modelled as sell minus 200, so absolute THB are approximate.
"""

from __future__ import annotations

import argparse
import itertools
import uuid

import numpy as np
import pandas as pd

from . import indicators, intl, plan, signals
from .config import settings

REGIMES = (
    ("2006-11 bull", "2006-01-01", "2011-08-31"),
    ("2011-18 bear", "2011-09-01", "2018-12-31"),
    ("2019-26 bull", "2019-01-01", "2100-01-01"),
)
SELECT_END = pd.Timestamp("2020-01-01")   # selection sees campaigns starting before this only
NBHD = 63                                  # half-width of the centred window for day skill
CAMPAIGNS = {"3m": 63, "6m": 126, "12m": 252}
STEP = 5                                   # a campaign starts every N trading days
GRID = {
    "rank_window": (None, 756),            # percentile over all history vs the last ~3 years
    "trigger": ("rich", "very_rich"),
    "brake_k": (1.0, 1.5),
    "grace": (5, 10),
}
_NS = uuid.UUID("00000000-0000-0000-0000-00000000ba5e")


# ----- data ----------------------------------------------------------------------------

def load_frame(sb) -> tuple[pd.DataFrame, pd.Series]:
    """Indicators on the score basis, plus the realized price on the same trading days."""
    from .load import fetch_all

    ind = indicators.build(intl.load_intl_daily(sb), 0.0)
    gta = pd.DataFrame(fetch_all(sb, "gold_price_daily", "trade_date,bar_buy_close", "trade_date"))
    bid = pd.Series(gta["bar_buy_close"].astype(float).values, index=pd.to_datetime(gta["trade_date"]))
    realized = bid.reindex(ind.index, method="ffill", limit=3).fillna(ind["close"])
    return ind, realized


# ----- the two measurements --------------------------------------------------------------

def centred_mean(price: np.ndarray, k: int = NBHD) -> np.ndarray:
    return pd.Series(price).rolling(2 * k + 1, center=True).mean().to_numpy()


def day_skill(price: np.ndarray, fills: np.ndarray, nb: np.ndarray) -> float:
    """Mean of fill price / centred average around the fill day, minus 1. NaN-safe."""
    if not len(fills):
        return float("nan")
    r = price[fills] / nb[fills] - 1.0
    return float(np.nanmean(r)) if np.isfinite(r).any() else float("nan")


def run_campaigns(price: np.ndarray, verdict: np.ndarray, L: int, rules: plan.Rules, starts) -> tuple[np.ndarray, np.ndarray, list[np.ndarray]]:
    """Replay plan.decide over each campaign. Returns avg fill price, avg fill day, fills."""
    avg, day, fills = [], [], []
    for a in starts:
        sold, last, f = 0, None, []
        for t in range(L - 1):
            d = plan.decide(t, L, sold, last, verdict[a + t], rules)
            if d.action == "sell":
                f.extend([a + t + 1] * d.count)   # count > 1 only when behind; a replay never is
                sold += d.count
                last = t
        f = np.array(f, dtype=int)
        avg.append(price[f].mean())
        day.append((f - a).mean())
        fills.append(f)
    return np.array(avg), np.array(day), fills


def slot_mid(price: np.ndarray, L: int, n: int, starts) -> tuple[np.ndarray, np.ndarray, list[np.ndarray]]:
    """Each tranche at the middle of its slot: what a seller with no skill gets on average."""
    ends = (-1,) + plan.slot_ends(L, n)
    mids = np.array([(ends[k] + 1 + ends[k + 1]) // 2 + 1 for k in range(n)])
    fills = [a + mids for a in starts]
    return np.array([price[f].mean() for f in fills]), np.full(len(starts), mids.mean()), fills


def sell_all_at_end(price: np.ndarray, L: int, starts):
    fills = [np.array([a + L - 1]) for a in starts]
    return np.array([price[f].mean() for f in fills]), np.full(len(starts), L - 1.0), fills


# ----- evaluation ------------------------------------------------------------------------

def _regime_masks(dates: pd.DatetimeIndex) -> dict[str, np.ndarray]:
    m = {name: (dates >= a) & (dates <= b) for name, a, b in REGIMES}
    m["pre-2020"] = dates < SELECT_END
    m["2020+"] = dates >= SELECT_END
    m["all"] = np.ones(len(dates), bool)
    return m


def summarize(price, nb, avg, day, fills, bench_avg, start_dates) -> dict:
    """Per-regime edge vs the benchmark, average sale day and day skill of the fills."""
    edge = avg / bench_avg - 1.0
    out: dict = {}
    for name, m in _regime_masks(start_dates).items():
        f = np.concatenate([fills[i] for i in np.where(m)[0]]) if m.any() else np.array([], int)
        out[name] = {
            "edge_pct": round(float(edge[m].mean()) * 100, 3) if m.any() else None,
            "win_pct": round(float((edge[m] > 0).mean()) * 100, 1) if m.any() else None,
            "avg_day": round(float(day[m].mean()), 1) if m.any() else None,
            "skill_pct": round(day_skill(price, f, nb) * 100, 3),
            "n": int(m.sum()),
        }
    return out


def n_eff(n_campaigns: int, L: int) -> int:
    """Independent (non-overlapping) campaigns behind a statistic: windows overlap heavily."""
    return max(1, round(n_campaigns * STEP / L))


def evaluate_config(ind, price, nb, cfg: dict, L: int) -> dict:
    sc = signals.compute_scores(ind, rank_window=cfg["rank_window"], brake_k=cfg["brake_k"])
    verdict = sc["verdict"].reindex(ind.index).fillna("neutral").to_numpy()
    first = ind.index.get_loc(sc.index[0])
    starts = list(range(first, len(price) - L, STEP))
    rules = plan.Rules(n_tranches=settings.plan_tranches, trigger=cfg["trigger"], grace=cfg["grace"])
    dates = ind.index[starts]
    neutral = np.full(len(price), "neutral", dtype=object)

    end_avg, end_day, end_f = run_campaigns(price, neutral, L, rules, starts)
    res = {
        "plan_signal": run_campaigns(price, verdict, L, rules, starts),
        "plan_slot_end": (end_avg, end_day, end_f),
        "plan_slot_mid": slot_mid(price, L, rules.n_tranches, starts),
        "sell_all_at_end": sell_all_at_end(price, L, starts),
    }
    out = {k: summarize(price, nb, *v, end_avg, dates) for k, v in res.items()}
    # Edge of the signal against the no-skill benchmark: the comparison that isolates day choice.
    sig_avg, sig_day, sig_f = res["plan_signal"]
    out["plan_signal_vs_mid"] = summarize(price, nb, sig_avg, sig_day, sig_f, res["plan_slot_mid"][0], dates)
    out["_raw"] = {k: v for k, v in res.items()}
    out["_window"] = {"starts": starts, "L": L}
    return out


def passes(r: dict) -> tuple[bool, list[str]]:
    """Acceptance: positive day skill AND a win over the random-day benchmark, every regime."""
    why = []
    for name, _, _ in REGIMES:
        s = r["plan_signal"][name]["skill_pct"]
        e = r["plan_signal_vs_mid"][name]["edge_pct"]
        if not (s is not None and s > 0):
            why.append(f"{name}: day skill {s}%")
        if not (e is not None and e > 0):
            why.append(f"{name}: edge vs random day {e}%")
    return not why, why


def select(ind, price, nb) -> tuple[dict, list[tuple[dict, float]]]:
    """Choose the grid point with the best mean pre-2020 edge vs the random-day benchmark
    over the 3m and 6m campaigns (the live campaign is 3 months)."""
    scored = []
    keys = list(GRID)
    for combo in itertools.product(*(GRID[k] for k in keys)):
        cfg = dict(zip(keys, combo))
        e = np.mean([evaluate_config(ind, price, nb, cfg, CAMPAIGNS[h])["plan_signal_vs_mid"]["pre-2020"]["edge_pct"] for h in ("3m", "6m")])
        scored.append((cfg, float(e)))
    scored.sort(key=lambda x: -x[1])
    return scored[0][0], scored


def deployed_config() -> dict:
    return {
        "rank_window": signals.RANK_WINDOW,
        "trigger": plan.Rules().trigger,
        "brake_k": signals.BRAKE_K,
        "grace": plan.Rules().grace,
    }


# ----- storage for the dashboard ---------------------------------------------------------

def _rows(ind, price, results: dict[str, dict], cfg: dict) -> list[dict]:
    bw = settings.baht_weight
    rows = []
    for h, r in results.items():
        L = r["_window"]["L"]
        starts = r["_window"]["starts"]
        for strat in ("plan_signal", "plan_slot_end", "plan_slot_mid", "sell_all_at_end"):
            avg, _, _ = r["_raw"][strat]
            lo = np.array([price[a : a + L].min() for a in starts])
            hi = np.array([price[a : a + L].max() for a in starts])
            cap = np.where(hi > lo, (avg - lo) / np.where(hi > lo, hi - lo, 1), 1.0)
            regret = hi - avg
            end_avg = r["_raw"]["plan_slot_end"][0]
            rows.append(
                {
                    "id": str(uuid.uuid5(_NS, f"v4|{strat}|{h}")),
                    "strategy": strat,
                    "params": {
                        "by_regime": r[strat],
                        "vs_random_day": r["plan_signal_vs_mid"] if strat == "plan_signal" else None,
                        "config": {k: v for k, v in cfg.items()},
                        "n_eff": n_eff(len(starts), L),
                        "tranches": settings.plan_tranches,
                        "score_version": signals.SCORE_VERSION,
                    },
                    "horizon_days": L,
                    "start_date": str(ind.index[starts[0]].date()),
                    "end_date": str(ind.index[-1].date()),
                    "median_thb": round(float(np.median(avg)) * bw, 2),
                    "median_capture_pct": round(float(np.median(cap)), 4),
                    "median_regret_thb": round(float(np.median(regret)) * bw, 2),
                    "p90_regret_thb": round(float(np.quantile(regret, 0.9)) * bw, 2),
                    "win_rate_vs_dca": None if strat == "plan_slot_end" else round(float((avg > end_avg).mean()), 4),
                }
            )
    return rows


def write_runs(sb, rows: list[dict]) -> None:
    """Replace backtest_runs wholesale: rows from a retired formula would otherwise sit
    beside the current ones on the dashboard (backtest_windows cascades)."""
    sb.table("backtest_runs").delete().gte("horizon_days", 0).execute()
    sb.table("backtest_runs").insert(rows).execute()


# ----- CLI -------------------------------------------------------------------------------

def _fmt(r: dict, key: str) -> str:
    cells = [f"{r[name][key]:+.2f}" if r[name][key] is not None else "  n/a" for name, _, _ in REGIMES]
    return " | ".join(f"{c:>8}" for c in cells)


def report(results: dict[str, dict]) -> None:
    regimes = " | ".join(f"{n:>8}" for n, _, _ in (("06-11", 0, 0), ("11-18", 0, 0), ("19-26", 0, 0)))
    for h, r in results.items():
        L = r["_window"]["L"]
        print(f"\n=== {h} campaigns ({L} trading days, {settings.plan_tranches} tranches) · n_eff ≈ {n_eff(len(r['_window']['starts']), L)}")
        print(f"{'':28} {'edge vs slot-end %':>32}   {'day skill % (±63d)':>32}   avg day")
        print(f"{'':28} {regimes}   {regimes}")
        for strat in ("plan_signal", "plan_slot_mid", "plan_slot_end", "sell_all_at_end"):
            print(f"{strat:28} {_fmt(r[strat], 'edge_pct')}   {_fmt(r[strat], 'skill_pct')}   {r[strat]['all']['avg_day']:>6}")
        print(f"{'plan_signal vs random day':28} {_fmt(r['plan_signal_vs_mid'], 'edge_pct')}")
        ok, why = passes(r)
        print("ACCEPT: " + ("PASS" if ok else "FAIL — " + "; ".join(why)))


def main() -> None:
    from . import load

    ap = argparse.ArgumentParser(prog="python -m etl.backtest")
    ap.add_argument("--select", action="store_true", help="re-run the pre-2020 grid search")
    ap.add_argument("--write", action="store_true", help="replace backtest_runs with these results (prod write)")
    args = ap.parse_args()

    sb = load.client()
    ind, realized = load_frame(sb)
    price = realized.to_numpy(dtype=float)
    nb = centred_mean(price)

    cfg = deployed_config()
    if args.select:
        best, scored = select(ind, price, nb)
        print("Grid (pre-2020 mean edge vs random day, 3m+6m):")
        for c, e in scored:
            print(f"  {e:+.3f}%  {c}{'   <- best' if c == best else ''}{'   <- deployed' if c == cfg else ''}")
        if best != cfg:
            print(f"\nNOTE: the deployed config differs from the pre-2020 best {best}. Update signals.py / plan.Rules and bump SCORE_VERSION.")

    results = {h: evaluate_config(ind, price, nb, cfg, L) for h, L in CAMPAIGNS.items()}
    print(f"\nDeployed config: {cfg} · holding {settings.gold_grams:g} g · realized = association bid, T+1 fills")
    report(results)
    if args.write:
        write_runs(sb, _rows(ind, price, results, cfg))
        print(f"\nWrote {len(results) * 4} rows to backtest_runs.")


if __name__ == "__main__":
    main()
