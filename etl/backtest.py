"""Backtest harness for the 'sell what you hold' problem.

One buy already happened; the decision is WHEN, inside a 3-12 month window, to
convert held gold to THB. Metrics are cash-oriented (realized THB, % of the
window range captured, regret vs the window high) — not Sharpe. The signal rule
(trailing-stop-from-peak) is pitted against the benchmarks that matter, above all
DCA-OUT. Results are written to backtest_runs / backtest_windows.

Honesty: overlapping windows are heavily autocorrelated, so aggregate CIs are
wide and we also report an out-of-sample split. Full CPCV is a later hardening step.
"""

from __future__ import annotations

import uuid

import numpy as np
import pandas as pd

from .config import settings

HORIZONS = {"3m": 63, "6m": 126, "9m": 189, "12m": 252}
TRAIL_X = [0.03, 0.05, 0.08, 0.10]
SCORE_T = [45, 50, 55, 60]
STEP = 3            # sample window starts every N trading days (power vs runtime)
_NS = uuid.UUID("00000000-0000-0000-0000-00000000ba5e")


# ----- strategies: each returns the realized (avg) sell price for one window seg -----

def s_random(seg: np.ndarray) -> float:
    return float(seg.mean())                       # expectation of a uniform sell day


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
            if v <= peak * (1 - x):           # accelerate on stop
                sold.append(v); nxt += 1; peak = v
            elif t >= sched[nxt]:             # scheduled tranche
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
        # benchmark windows
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
            strat = name.rsplit("_", 1)[0] if name[-1].isdigit() else name
            param = name.split("_")[-1] if name[-1].isdigit() else None
            agg = _agg(w, None if name == "dca_out" else dca_sell)
            rid = uuid.uuid5(_NS, f"{name}|{hname}")
            runs.append(
                {
                    "id": str(rid),
                    "strategy": name,
                    "params": {"x_pct": param} if "trail" in name else ({"t": param} if "score" in name else {}),
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

        # calibrate (capture the high = max median realized THB)
        best_aao = max(TRAIL_X, key=lambda x: configs[f"trail_aao_{int(x*100)}"]["sell_price"].median())
        best_lad = max(TRAIL_X, key=lambda x: configs[f"trail_ladder_{int(x*100)}"]["sell_price"].median())
        best_t = max(SCORE_T, key=lambda t: configs[f"score_ge_{t}"]["sell_price"].median())

        summary[hname] = {
            "dca": configs["dca_out"]["sell_price"].median() * bw,
            "aao": (best_aao, configs[f"trail_aao_{int(best_aao*100)}"]["sell_price"].median() * bw,
                    configs[f"trail_aao_{int(best_aao*100)}"]["regret_thb"].median() * bw),
            "ladder": (best_lad, configs[f"trail_ladder_{int(best_lad*100)}"]["sell_price"].median() * bw,
                       configs[f"trail_ladder_{int(best_lad*100)}"]["regret_thb"].median() * bw,
                       _agg(configs[f"trail_ladder_{int(best_lad*100)}"], dca_sell)["win_rate_vs_dca"]),
            "score_t": best_t,
        }

        # store per-window detail for the recommended laddered config at this horizon
        rec = configs[f"trail_ladder_{int(best_lad*100)}"]
        rid = uuid.uuid5(_NS, f"trail_ladder_{int(best_lad*100)}|{hname}")
        for r in rec.itertuples(index=False):
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

    # write (idempotent: deterministic ids)
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
    print(f"Backtest holding = {settings.gold_grams:g} g ({bw:.2f} baht-weight). Median realized THB for the full holding:\n")
    print(f"{'horizon':>8} | {'DCA-out':>12} | {'trail aao':>20} | {'laddered (recommended)':>30}")
    print("-" * 80)
    for h in HORIZONS:
        d = s[h]
        aao_x, aao_thb, _ = d["aao"]
        lx, lthb, lreg, lwin = d["ladder"]
        print(f"{h:>8} | {d['dca']:>12,.0f} | {f'{aao_thb:,.0f} (x={int(aao_x*100)}%)':>20} | "
              f"{f'{lthb:,.0f} (x={int(lx*100)}%, beat DCA {lwin*100:.0f}%)':>30}")
    print(f"\nCalibrated composite score sell-threshold (capture-the-high): "
          f"{ {h: s[h]['score_t'] for h in HORIZONS} }")
    print(f"Wrote {s['_counts']['runs']} runs, {s['_counts']['windows']} windows.")


if __name__ == "__main__":
    main()
