"""Backtest harness for the 'sell what you hold' problem.

One buy already happened; the decision is WHEN, inside a 3-12 month window, to
convert held gold to THB. Metrics are cash-oriented (realized THB, % of the
window range captured, regret vs the window high) — not Sharpe. The signal rule
(trailing-stop-from-peak) is pitted against the benchmarks that matter, above all
DCA-OUT. Results are written to backtest_runs / backtest_windows.

Rigor: overlapping windows are heavily autocorrelated, so beyond full-sample
medians we report (a) an out-of-sample holdout (window starts >= 2020) and
(b) an embargoed k-fold capture (purged CV) to gauge stability across regimes.
Both are stored in backtest_runs.params and printed.
"""

from __future__ import annotations

import uuid

import numpy as np
import pandas as pd

from .config import settings

HORIZONS = {"3m": 63, "6m": 126, "9m": 189, "12m": 252}
TRAIL_X = [0.03, 0.05, 0.08, 0.10]
SCORE_T = [45, 50, 55, 60]
STEP = 3                              # sample window starts every N trading days
OOS_START = pd.Timestamp("2020-01-01")  # out-of-sample holdout boundary
_NS = uuid.UUID("00000000-0000-0000-0000-00000000ba5e")


# ----- strategies: each returns the realized (avg) sell price for one window seg -----

def s_random(seg: np.ndarray) -> float:
    return float(seg.mean())


def s_end(seg: np.ndarray) -> float:
    return float(seg[-1])


def s_dca(seg: np.ndarray, n: int = 6) -> float:
    idx = np.linspace(0, len(seg) - 1, n).round().astype(int)
    return float(seg[idx].mean())


def s_trail_aao(seg: np.ndarray, x: float) -> float:
    peak = seg[0]
    for v in seg:
        peak = max(peak, v)
        if v <= peak * (1 - x):
            return float(v)
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
                sold.append(v); nxt += 1; peak = v
            elif t >= sched[nxt]:
                sold.append(v); nxt += 1
    while nxt < n:
        sold.append(seg[-1]); nxt += 1
    return float(np.mean(sold))


def s_score(seg: np.ndarray, score_seg: np.ndarray, t: float) -> float:
    hits = np.where(~np.isnan(score_seg) & (score_seg >= t))[0]
    return float(seg[hits[0]]) if len(hits) else float(seg[-1])


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
    score = pd.DataFrame(_fetch_all(sb, "signals_daily", "trade_date,sell_pressure", "trade_date"))
    df = price.merge(score, on="trade_date", how="left")
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df.set_index("trade_date").astype({"bar_buy_close": float, "sell_pressure": float})


# ----- evaluation -----

def _eval(price: np.ndarray, score: np.ndarray, dates: np.ndarray, length: int, realize) -> pd.DataFrame:
    out = []
    for i in range(0, len(price) - length + 1, STEP):
        seg = price[i : i + length]
        sc = score[i : i + length]
        wmin, wmax = seg.min(), seg.max()
        rng = wmax - wmin
        sell = realize(seg, sc)
        out.append(
            {
                "window_start": dates[i],
                "window_end": dates[i + length - 1],
                "sell_price": sell,
                "window_min": wmin,
                "window_max": wmax,
                "capture_pct": (sell - wmin) / rng if rng else 1.0,
                "regret_thb": wmax - sell,
            }
        )
    return pd.DataFrame(out)


def _agg(w: pd.DataFrame, dca_sell: pd.Series | None) -> dict:
    d = {
        "median_thb": w["sell_price"].median(),
        "median_capture_pct": w["capture_pct"].median(),
        "median_regret_thb": w["regret_thb"].median(),
        "p90_regret_thb": w["regret_thb"].quantile(0.90),
    }
    if dca_sell is not None:
        d["win_rate_vs_dca"] = float((w["sell_price"].values > dca_sell.values).mean())
    return d


def _oos_capture(w: pd.DataFrame) -> float | None:
    oos = w[w["window_start"] >= OOS_START]
    return round(float(oos["capture_pct"].median()), 4) if len(oos) else None


def _cv_capture(w: pd.DataFrame, horizon_days: int, k: int = 5) -> dict | None:
    """Embargoed k-fold (purged CV): median capture per contiguous time block, with the
    overlap-length tail of each block dropped so adjacent folds don't leak."""
    w = w.sort_values("window_start").reset_index(drop=True)
    n = len(w)
    if n < k * 3:
        return None
    emb = max(1, horizon_days // STEP)
    caps = []
    for i in range(k):
        lo = int(n * i / k)
        hi = int(n * (i + 1) / k)
        fold = w.iloc[lo : max(lo + 1, hi - emb)]
        if len(fold):
            caps.append(float(fold["capture_pct"].median()))
    if not caps:
        return None
    return {"cv_mean": round(float(np.mean(caps)), 4), "cv_std": round(float(np.std(caps)), 4)}


def run_backtest(sb) -> dict:
    df = load_series(sb)
    price = df["bar_buy_close"].values
    score = df["sell_pressure"].values
    dates = df.index
    bw = settings.baht_weight

    runs: list[dict] = []
    win_store: list[dict] = []
    summary: dict = {}

    for hname, L in HORIZONS.items():
        w_dca = _eval(price, score, dates, L, lambda s, sc: s_dca(s))
        dca_sell = w_dca["sell_price"]
        configs: dict[str, pd.DataFrame] = {
            "random_day": _eval(price, score, dates, L, lambda s, sc: s_random(s)),
            "window_end": _eval(price, score, dates, L, lambda s, sc: s_end(s)),
            "dca_out": w_dca,
        }
        for x in TRAIL_X:
            configs[f"trail_aao_{int(x*100)}"] = _eval(price, score, dates, L, lambda s, sc, x=x: s_trail_aao(s, x))
            configs[f"trail_ladder_{int(x*100)}"] = _eval(price, score, dates, L, lambda s, sc, x=x: s_trail_ladder(s, x))
        for t in SCORE_T:
            configs[f"score_ge_{t}"] = _eval(price, score, dates, L, lambda s, sc, t=t: s_score(s, sc, t))

        for name, w in configs.items():
            param = name.split("_")[-1] if name[-1].isdigit() else None
            agg = _agg(w, None if name == "dca_out" else dca_sell)
            params: dict = {}
            if "trail" in name:
                params["x_pct"] = param
            elif "score" in name:
                params["t"] = param
            params["oos_capture_pct"] = _oos_capture(w)
            if "trail_ladder" in name or "score" in name:
                cv = _cv_capture(w, L)
                if cv:
                    params.update(cv)
            rid = uuid.uuid5(_NS, f"{name}|{hname}")
            runs.append(
                {
                    "id": str(rid),
                    "strategy": name,
                    "params": params,
                    "horizon_days": L,
                    "start_date": str(dates[0].date()),
                    "end_date": str(dates[-1].date()),
                    "median_thb": round(agg["median_thb"], 2),
                    "median_capture_pct": round(agg["median_capture_pct"], 4),
                    "median_regret_thb": round(agg["median_regret_thb"], 2),
                    "p90_regret_thb": round(agg["p90_regret_thb"], 2),
                    "win_rate_vs_dca": round(agg.get("win_rate_vs_dca"), 4) if agg.get("win_rate_vs_dca") is not None else None,
                }
            )

        # calibrate on full sample (capture the high = max median realized THB)
        best_aao = max(TRAIL_X, key=lambda x: configs[f"trail_aao_{int(x*100)}"]["sell_price"].median())
        best_lad = max(TRAIL_X, key=lambda x: configs[f"trail_ladder_{int(x*100)}"]["sell_price"].median())
        best_t = max(SCORE_T, key=lambda t: configs[f"score_ge_{t}"]["sell_price"].median())
        lad_w = configs[f"trail_ladder_{int(best_lad*100)}"]
        score_w = configs[f"score_ge_{best_t}"]

        summary[hname] = {
            "dca": configs["dca_out"]["sell_price"].median() * bw,
            "aao": (best_aao, configs[f"trail_aao_{int(best_aao*100)}"]["sell_price"].median() * bw),
            "ladder": (best_lad, lad_w["sell_price"].median() * bw, _agg(lad_w, dca_sell)["win_rate_vs_dca"]),
            "score_t": best_t,
            "is_capture": round(float(score_w["capture_pct"].median()), 4),
            "oos_capture": _oos_capture(score_w),
            "cv": _cv_capture(score_w, L),
        }

        # store per-window detail for the recommended laddered config at this horizon
        rid = uuid.uuid5(_NS, f"trail_ladder_{int(best_lad*100)}|{hname}")
        for r in lad_w.itertuples(index=False):
            win_store.append(
                {
                    "run_id": str(rid),
                    "window_start": str(r.window_start.date()),
                    "window_end": str(r.window_end.date()),
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
    print(f"Backtest holding = {settings.gold_grams:g} g ({bw:.2f} baht-weight).\n")
    print(f"{'horizon':>8} | {'DCA-out THB':>13} | {'laddered THB (x, beat DCA)':>30} | {'score-rule capture IS / OOS / cv':>34}")
    print("-" * 98)
    for h in HORIZONS:
        d = s[h]
        lx, lthb, lwin = d["ladder"]
        cv = d["cv"]
        cv_s = f"{cv['cv_mean']*100:.0f}±{cv['cv_std']*100:.0f}%" if cv else "n/a"
        oos = f"{d['oos_capture']*100:.0f}%" if d["oos_capture"] is not None else "n/a"
        print(f"{h:>8} | {d['dca']:>13,.0f} | {f'{lthb:,.0f} (x={int(lx*100)}%, {lwin*100:.0f}%)':>30} | "
              f"{f'T={d['score_t']}: {d['is_capture']*100:.0f}% / {oos} / {cv_s}':>34}")
    print(f"\nWrote {s['_counts']['runs']} runs, {s['_counts']['windows']} windows. (OOS = window starts >= 2020)")


if __name__ == "__main__":
    main()
