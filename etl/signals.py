"""Composite 0-100 sell-pressure score + verdict.

Trend-break-weighted by design: gold is in a strong secular uptrend where
overbought/mean-reversion signals fire too early, so trailing-stop / trend-break
exits dominate the score and correlated oscillators are collapsed into one
overbought sub-score (not triple-counted). Thresholds here are sensible defaults;
Phase 3 backtesting calibrates them for the "capture the high" objective.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

WEIGHTS = {"trend_break": 0.45, "overbought": 0.30, "momentum": 0.20, "seasonality": 0.05}


def _seasonality(close: pd.Series) -> pd.Series:
    """Data-driven month tilt: historically weak months -> higher sell pressure."""
    monthly = close.resample("ME").last().pct_change()
    by_month = monthly.groupby(monthly.index.month).mean()
    span = by_month.max() - by_month.min()
    if span <= 0:
        scale = by_month * 0 + 50.0
    else:
        scale = (by_month.max() - by_month) / span * 100.0
    return pd.Series(close.index.month, index=close.index).map(scale)


def compute_scores(ind: pd.DataFrame) -> pd.DataFrame:
    c = ind["close"]

    # --- trend break (5 equally-weighted binary exits) ---
    tb = pd.DataFrame(
        {
            "below_chandelier": c < ind["chandelier_w"],
            "below_donchian_10w": c < ind["donchian_low_10w"],
            "below_donchian_20w": c < ind["donchian_low_20w"],
            "death_cross": ind["death_cross"].astype(bool),
            "below_200dma": ind["below_200dma"].astype(bool),
        }
    )
    n_trend = tb.sum(axis=1)
    trend_break = n_trend / len(tb.columns) * 100

    # --- overbought stretch (correlated oscillators collapsed via mean) ---
    overbought = pd.concat(
        [
            (ind["stretch_200"] / 0.26).clip(0, 1) * 100,      # 26% above 200-DMA -> 100
            ((ind["rsi14_w"] - 50) / 30).clip(0, 1) * 100,     # weekly RSI 50->0, 80->100
            ((ind["pctb_w"] - 0.5) / 0.5).clip(0, 1) * 100,    # %B 0.5->0, 1.0->100
            (ind["roc252"] / 0.50).clip(0, 1) * 100,           # +50% YoY -> 100
        ],
        axis=1,
    ).mean(axis=1)

    # --- momentum rollover (weekly MACD): two INDEPENDENT reads, each 0/50 ---
    # below the signal line (turning down) + below the zero line (confirmed bear territory).
    # (Previously the 2nd term used hist<0, which equals macd<signal — redundant, so momentum
    # was only ever 0 or 100. Using the zero-line makes it graded: 0 / 50 / 100.)
    momentum = (ind["macd_w"] < ind["macd_sig_w"]).astype(float) * 50 + (ind["macd_w"] < 0).astype(float) * 50

    seasonality = _seasonality(c)

    composite = (
        WEIGHTS["trend_break"] * trend_break
        + WEIGHTS["overbought"] * overbought
        + WEIGHTS["momentum"] * momentum
        + WEIGHTS["seasonality"] * seasonality
    )

    # require core weekly + 200-DMA history before a score is meaningful
    valid = (
        ind["chandelier_w"].notna()
        & ind["donchian_low_20w"].notna()
        & ind["sma200"].notna()
        & ind["rsi14_w"].notna()
        & ind["macd_sig_w"].notna()
        & ind["roc252"].notna()   # ensure the overbought mean averages all 4 inputs, not fewer
        & ind["pctb_w"].notna()
    )

    verdict = np.select(
        [(n_trend >= 2) & (composite >= 55), composite >= 45, composite >= 35],
        ["sell", "sell_tranche", "trim"],
        default="hold",
    )

    flags = pd.DataFrame(
        {
            **{k: tb[k] for k in tb.columns},
            "rsi_weekly_gt70": ind["rsi14_w"] > 70,
            "stretch_gt18pct": ind["stretch_200"] > 0.18,
            "pctb_gt1": ind["pctb_w"] > 1.0,
            "macd_bearish": ind["macd_w"] < ind["macd_sig_w"],
        }
    )
    active = flags.apply(lambda r: [k for k, v in r.items() if bool(v)], axis=1)

    res = pd.DataFrame(
        {
            "sell_pressure": composite.round(2),
            "trend_break": trend_break.round(2),
            "overbought": overbought.round(2),
            "momentum": momentum.round(2),
            "seasonality": seasonality.round(2),
            "verdict": pd.Series(verdict, index=ind.index),
            "n_trend": n_trend,
            "active_signals": active,
        }
    )
    return res[valid]


def _clean(v: object) -> object:
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def upsert_signals(sb, scores: pd.DataFrame) -> int:
    records = []
    for idx, row in scores.iterrows():
        records.append(
            {
                "trade_date": idx.date().isoformat(),
                "sell_pressure": _clean(row["sell_pressure"]),
                "trend_break": _clean(row["trend_break"]),
                "overbought": _clean(row["overbought"]),
                "momentum": _clean(row["momentum"]),
                "seasonality": _clean(row["seasonality"]),
                "verdict": row["verdict"],
                "active_signals": list(row["active_signals"]),
            }
        )
    for i in range(0, len(records), 1000):
        sb.table("signals_daily").upsert(records[i : i + 1000], on_conflict="trade_date").execute()
    return len(records)
