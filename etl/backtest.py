"""Backtest harness for the 'sell what you hold' problem.

One buy already happened; the decision is WHEN, inside a 3-12 month window, to
convert held gold to THB. Metrics are cash-oriented (realized THB, % of the
window range captured, regret vs the window high) — not Sharpe. The candidate
rules are pitted against the benchmarks that matter, above all DCA-OUT.

Post-audit rigor (why the old headline numbers were overstated):
  - EXECUTION LAG: every rule executes at the NEXT Thai trading day's price, not
    the same close the signal is built from (the intl score's basis, the LBMA PM
    fix, publishes hours after the association close — same-day fills were a
    look-ahead that flattered trailing/score rules on falling tape).
  - CLEAN SELECTION: the best threshold / trail knob is chosen ONLY on pre-2020
    windows, then the >=2020 holdout is reported for that pre-chosen config — so
    the holdout no longer validates the parameter it helped pick.
  - HONEST UNCERTAINTY: windows overlap ~99% (STEP=3), so point medians are
    near-duplicate; we attach a circular block-bootstrap CI (block>=horizon) to
    win-rate-vs-DCA and capture, and report each score rule's TRIGGER RATE so a
    'good' number that is really just 'held to window end' is visible.
  - REAL POLICY: we backtest the DEPLOYED ladder (trim@T1 / tranche@T2 / sell@T3
    with the n_trend>=2 gate, laddered fractions), not only sell-all-at-one-T.

The score BASIS is international THB; the realized price is the association bid
(bar_buy_close) — that is what Poom actually sells into.
"""

from __future__ import annotations

import uuid

import numpy as np
import pandas as pd

from . import signals
from .config import settings

HORIZONS = {"3m": 63, "6m": 126, "9m": 189, "12m": 252}
TRAIL_X = [0.03, 0.05, 0.08, 0.10]
SCORE_T = [35, 40, 45, 50, 55, 60]
# Joint (trim, tranche, sell) grids for the deployed ladder policy, incl. the shipped one.
LADDER_GRID = [
    (signals.T_TRIM, signals.T_TRANCHE, signals.T_SELL),
    (38, 46, 54), (40, 48, 56), (42, 50, 58), (44, 52, 60), (46, 54, 62),
]
LADDER_W = (0.34, 0.33)          # trim sells 34%, tranche 33%, sell dumps the remaining 33%
STEP = 3                              # sample window starts every N trading days
OOS_START = pd.Timestamp("2020-01-01")  # out-of-sample holdout boundary
BOOT_N = 400                         # block-bootstrap resamples
_NS = uuid.UUID("00000000-0000-0000-0000-00000000ba5e")


# ----- strategies: each returns the realized (avg) sell price for one window seg -----
# `nxt` executes a trigger at the NEXT day's price (index+1), else the last day.

def _nxt(seg: np.ndarray, i: int) -> float:
    return float(seg[i + 1]) if i + 1 < len(seg) else float(seg[-1])


def s_random(seg: np.ndarray) -> float:
    return float(seg.mean())


def s_end(seg: np.ndarray) -> float:
    return float(seg[-1])


def s_dca(seg: np.ndarray, n: int = 6) -> float:
    idx = np.linspace(0, len(seg) - 1, n).round().astype(int)
    return float(seg[idx].mean())


def s_trail_aao(seg: np.ndarray, x: float) -> float:
    peak = seg[0]
    for i, v in enumerate(seg):
        peak = max(peak, v)
        if v <= peak * (1 - x):
            return _nxt(seg, i)
    return float(seg[-1])


def s_trail_ladder(seg: np.ndarray, x: float, n: int = 4) -> float:
    """DCA-out floor of n tranches; a trailing-stop trigger ACCELERATES the next tranche."""
    sched = np.linspace(0, len(seg) - 1, n).round().astype(int)
    sold: list[float] = []
    peak = seg[0]
    nxt = 0
    for t, v in enumerate(seg):
        peak = max(peak, v)
        if nxt < n:
            if v <= peak * (1 - x):
                sold.append(_nxt(seg, t)); nxt += 1; peak = v
            elif t >= sched[nxt]:
                sold.append(_nxt(seg, t)); nxt += 1
    while nxt < n:
        sold.append(seg[-1]); nxt += 1
    return float(np.mean(sold))


def s_score(seg: np.ndarray, score_seg: np.ndarray, t: float) -> float:
    hits = np.where(~np.isnan(score_seg) & (score_seg >= t))[0]
    return _nxt(seg, hits[0]) if len(hits) else float(seg[-1])


def s_ladder(seg: np.ndarray, score_seg: np.ndarray, ntrend_seg: np.ndarray, T, w=LADDER_W) -> float:
    """The DEPLOYED verdict machine as an execution policy: fraction w[0] at the first
    trim crossing, w[1] at the first tranche crossing, the remainder at the first gated
    sell; anything unsold is dumped at the window end. Each tranche fills at T+1."""
    n = len(seg)
    held, proceeds = 1.0, 0.0
    fired_trim = fired_tr = fired_sell = False
    for i in range(n):
        s = score_seg[i]
        if np.isnan(s):
            continue
        if not fired_trim and s >= T[0]:
            proceeds += w[0] * _nxt(seg, i); held -= w[0]; fired_trim = True
        if not fired_tr and s >= T[1]:
            proceeds += w[1] * _nxt(seg, i); held -= w[1]; fired_tr = True
        if not fired_sell and s >= T[2] and ntrend_seg[i] >= 2:
            proceeds += held * _nxt(seg, i); held = 0.0; fired_sell = True
        if held <= 1e-9:
            break
    if held > 1e-9:
        proceeds += held * float(seg[-1])
    return proceeds


def _score_triggers(score_seg: np.ndarray, t: float) -> bool:
    return bool(np.any(~np.isnan(score_seg) & (score_seg >= t)))


# ----- data -----

def _fetch_all(sb, table: str, cols: str, order: str) -> list[dict]:
    rows, page = [], 0
    while True:
        res = sb.table(table).select(cols).order(order).range(page * 1000, page * 1000 + 999).execute()
        rows.extend(res.data)
        if len(res.data) < 1000:
            return rows
        page += 1


def load_series(sb) -> pd.DataFrame:
    price = pd.DataFrame(_fetch_all(sb, "gold_price_daily", "trade_date,bar_buy_close", "trade_date"))
    score = pd.DataFrame(_fetch_all(sb, "signals_daily", "trade_date,sell_pressure,active_signals", "trade_date"))
    # reconstruct n_trend from the stored flags (no dedicated column): a fresh trailing
    # break + a confirmed secular bear are the two gate inputs.
    def ntrend(sig) -> int:
        a = sig or []
        return int("trailing_stop_fired" in a) + int("secular_confirm" in a)
    score["n_trend"] = score["active_signals"].map(ntrend)
    df = price.merge(score[["trade_date", "sell_pressure", "n_trend"]], on="trade_date", how="left")
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.set_index("trade_date").astype({"bar_buy_close": float, "sell_pressure": float})
    df["n_trend"] = df["n_trend"].fillna(0).astype(int)
    first = df["sell_pressure"].first_valid_index()
    return df.loc[first:] if first is not None else df


# ----- evaluation -----

def _eval(price, score, ntrend, dates, length, realize) -> pd.DataFrame:
    out = []
    for i in range(0, len(price) - length + 1, STEP):
        seg = price[i : i + length]
        sc = score[i : i + length]
        nt = ntrend[i : i + length]
        wmin, wmax = seg.min(), seg.max()
        rng = wmax - wmin
        sell = realize(seg, sc, nt)
        out.append(
            {
                "window_start": dates[i],
                "sell_price": sell,
                "window_min": wmin,
                "window_max": wmax,
                "capture_pct": (sell - wmin) / rng if rng else 1.0,
                "regret_pct": (wmax - sell) / rng if rng else 0.0,
                "regret_thb": wmax - sell,
            }
        )
    return pd.DataFrame(out)


def _is_oos(w: pd.DataFrame):
    return w["window_start"] < OOS_START, w["window_start"] >= OOS_START


def _block_boot(values: np.ndarray, horizon: int, stat=np.median) -> tuple[float, float, float]:
    """Circular block bootstrap CI for a statistic over overlapping windows."""
    n = len(values)
    if n < 5:
        return float(stat(values)) if n else float("nan"), float("nan"), float("nan")
    block = max(1, horizon // STEP)
    nblocks = int(np.ceil(n / block))
    # deterministic pseudo-random starts (no RNG dependency): stride the index space
    ests = []
    for r in range(BOOT_N):
        idx = []
        for bkr in range(nblocks):
            start = (r * 2654435761 + bkr * 40503) % n   # LCG-ish spread, reproducible
            idx.extend((start + k) % n for k in range(block))
        ests.append(float(stat(values[np.array(idx[:n])])))
    lo, hi = np.percentile(ests, [2.5, 97.5])
    return float(stat(values)), float(lo), float(hi)


def _win_rate(w: pd.DataFrame, dca_sell: pd.Series) -> np.ndarray:
    return (w["sell_price"].values > dca_sell.values).astype(float)


def run_backtest(sb) -> dict:
    df = load_series(sb)
    price = df["bar_buy_close"].values
    score = df["sell_pressure"].values
    ntrend = df["n_trend"].values
    dates = df.index
    bw = settings.baht_weight

    runs: list[dict] = []
    win_store: list[dict] = []
    summary: dict = {}

    for hname, L in HORIZONS.items():
        w_dca = _eval(price, score, ntrend, dates, L, lambda s, sc, nt: s_dca(s))
        dca_sell = w_dca["sell_price"]
        configs: dict[str, pd.DataFrame] = {
            "random_day": _eval(price, score, ntrend, dates, L, lambda s, sc, nt: s_random(s)),
            "window_end": _eval(price, score, ntrend, dates, L, lambda s, sc, nt: s_end(s)),
            "dca_out": w_dca,
        }
        for x in TRAIL_X:
            configs[f"trail_aao_{int(x*100)}"] = _eval(price, score, ntrend, dates, L, lambda s, sc, nt, x=x: s_trail_aao(s, x))
            configs[f"trail_ladder_{int(x*100)}"] = _eval(price, score, ntrend, dates, L, lambda s, sc, nt, x=x: s_trail_ladder(s, x))
        for t in SCORE_T:
            configs[f"score_ge_{t}"] = _eval(price, score, ntrend, dates, L, lambda s, sc, nt, t=t: s_score(s, sc, t))
        for T in LADDER_GRID:
            key = f"ladder_{T[0]}_{T[1]}_{T[2]}"
            configs[key] = _eval(price, score, ntrend, dates, L, lambda s, sc, nt, T=T: s_ladder(s, sc, nt, T))

        for name, w in configs.items():
            is_mask, oos_mask = _is_oos(w)
            wr = None if name == "dca_out" else _win_rate(w, dca_sell)
            params: dict = {}
            if "trail" in name:
                params["x_pct"] = name.split("_")[-1]
            elif name.startswith("score_ge"):
                params["t"] = name.split("_")[-1]
                params["trigger_rate"] = round(float(np.mean([
                    _score_triggers(score[i:i+L], float(params["t"]))
                    for i in range(0, len(price) - L + 1, STEP)])), 4)
            elif name.startswith("ladder_"):
                params["T"] = name.split("_", 1)[1]
            params["is_capture"] = round(float(w.loc[is_mask, "capture_pct"].median()), 4) if is_mask.any() else None
            params["oos_capture"] = round(float(w.loc[oos_mask, "capture_pct"].median()), 4) if oos_mask.any() else None
            if wr is not None:
                m, lo, hi = _block_boot(wr, L, np.mean)
                params["win_vs_dca"] = round(m, 4)
                params["win_vs_dca_ci"] = [round(lo, 4), round(hi, 4)]
            rid = uuid.uuid5(_NS, f"{name}|{hname}")
            runs.append(
                {
                    "id": str(rid),
                    "strategy": name,
                    "params": params,
                    "horizon_days": L,
                    "start_date": str(dates[0].date()),
                    "end_date": str(dates[-1].date()),
                    "median_thb": round(float(w["sell_price"].median()) * bw, 2),
                    "median_capture_pct": round(float(w["capture_pct"].median()), 4),
                    "median_regret_thb": round(float(w["regret_thb"].median()) * bw, 2),
                    "p90_regret_thb": round(float(w["regret_thb"].quantile(0.90)) * bw, 2),
                    "win_rate_vs_dca": round(float(np.mean(wr)), 4) if wr is not None else None,
                }
            )

        # --- CLEAN SELECTION: choose on pre-2020 capture only, report OOS for that choice ---
        def is_cap(cfg: str) -> float:
            m, _ = _is_oos(configs[cfg])
            sub = configs[cfg].loc[m, "capture_pct"]
            return float(sub.median()) if len(sub) else -1.0

        best_t = max(SCORE_T, key=lambda t: is_cap(f"score_ge_{t}"))
        best_lad = max(LADDER_GRID, key=lambda T: is_cap(f"ladder_{T[0]}_{T[1]}_{T[2]}"))
        best_aao = max(TRAIL_X, key=lambda x: is_cap(f"trail_aao_{int(x*100)}"))
        lad_key = f"ladder_{best_lad[0]}_{best_lad[1]}_{best_lad[2]}"
        lad_w = configs[lad_key]
        _, lad_oos = _is_oos(lad_w)
        wr_lad = _win_rate(lad_w, dca_sell)
        m_wr, lo_wr, hi_wr = _block_boot(wr_lad, L, np.mean)

        summary[hname] = {
            "dca_thb": round(float(configs["dca_out"]["sell_price"].median()) * bw),
            "best_score_t": best_t,
            "score_is": is_cap(f"score_ge_{best_t}"),
            "score_oos": configs[f"score_ge_{best_t}"].loc[_is_oos(configs[f'score_ge_{best_t}'])[1], "capture_pct"].median(),
            "score_trigger": next(r["params"].get("trigger_rate") for r in runs if r["strategy"] == f"score_ge_{best_t}" and r["horizon_days"] == L),
            "best_ladder": best_lad,
            "ladder_is": is_cap(lad_key),
            "ladder_oos": round(float(lad_w.loc[lad_oos, "capture_pct"].median()), 4),
            "ladder_thb": round(float(lad_w["sell_price"].median()) * bw),
            "ladder_win_vs_dca": round(m_wr, 3),
            "ladder_win_ci": [round(lo_wr, 3), round(hi_wr, 3)],
            "best_aao": best_aao,
            "aao_is": is_cap(f"trail_aao_{int(best_aao*100)}"),
        }

        rid = uuid.uuid5(_NS, f"{lad_key}|{hname}")
        for r in lad_w.itertuples(index=False):
            win_store.append(
                {
                    "run_id": str(rid),
                    "window_start": str(r.window_start.date()),
                    "window_end": str((r.window_start + pd.Timedelta(days=1)).date()),
                    "sell_date": None,
                    "sell_price": round(r.sell_price, 2),
                    "window_min": round(r.window_min, 2),
                    "window_max": round(r.window_max, 2),
                    "capture_pct": round(r.capture_pct, 4),
                    "regret_thb": round(r.regret_thb, 2),
                }
            )

    for i in range(0, len(runs), 500):
        sb.table("backtest_runs").upsert(runs[i : i + 500], on_conflict="id").execute()
    for i in range(0, len(win_store), 1000):
        sb.table("backtest_windows").upsert(win_store[i : i + 1000], on_conflict="run_id,window_start").execute()

    summary["_counts"] = {"runs": len(runs), "windows": len(win_store)}
    return summary


def main() -> None:
    from . import load

    sb = load.client()
    s = run_backtest(sb)
    bw = settings.baht_weight
    print(f"Backtest holding = {settings.gold_grams:g} g ({bw:.2f} baht-weight). Realized @ association bid, T+1 fills.\n")
    print(f"{'horizon':>7} | {'DCA THB':>10} | {'best ladder (T)  cap IS/OOS  win-vs-DCA[CI]':>52} | {'best score_ge_T  cap IS/OOS (trig)':>36}")
    print("-" * 118)
    for h in HORIZONS:
        d = s[h]
        lad = d["best_ladder"]
        lad_s = f"({lad[0]}/{lad[1]}/{lad[2]}) {d['ladder_is']*100:.0f}%/{d['ladder_oos']*100:.0f}%  {d['ladder_win_vs_dca']*100:.0f}%[{d['ladder_win_ci'][0]*100:.0f}-{d['ladder_win_ci'][1]*100:.0f}]"
        sc_oos = f"{d['score_oos']*100:.0f}%" if pd.notna(d['score_oos']) else "n/a"
        sc_s = f"T={d['best_score_t']} {d['score_is']*100:.0f}%/{sc_oos} (trig {d['score_trigger']*100:.0f}%)"
        print(f"{h:>7} | {d['dca_thb']:>10,} | {lad_s:>52} | {sc_s:>36}")
    print(f"\nWrote {s['_counts']['runs']} runs, {s['_counts']['windows']} windows. Selection on pre-{OOS_START.year} capture; OOS = starts >= {OOS_START.year}.")


if __name__ == "__main__":
    main()
