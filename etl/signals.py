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

from .dxy import DOLLAR_SELL, band_of

WEIGHTS = {"trend_break": 0.40, "overbought": 0.25, "momentum": 0.18, "dollar": 0.12, "seasonality": 0.05}

# Peak-aware trailing-exit knobs (calibrated against capture-the-high in backtest.py).
TRAIL_X = 0.03      # a break "opens" once price is 3% below its recent high
TRAIL_BAND = 0.05   # breach saturates over the next 5% (3% -> 0, >=8% -> 1): continuous, no cliff
TRAIL_TAU = 20.0    # freshness half-life-ish in bars: a break fades as IT ages (~4 weeks)
PROX_KNEE = 0.06    # overbought is "near the high" within this drawdown, damped beyond it

# Verdict cut-offs, re-fit to the new (lower, smoother) composite distribution: the
# peak-aware score tops out ~64 (p99 ~49) instead of the old count-based ramp to 100.
# 'sell' aligns with the backtest's strongest-capture zone (T~50: 12m capture 69% IS /
# 82% OOS) and still requires n_trend>=2 (a fresh break AND a confirmed bear).
T_TRIM, T_TRANCHE, T_SELL = 33.0, 42.0, 50.0


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


def compute_scores(
    ind: pd.DataFrame,
    dxy: pd.Series | None = None,
    *,
    trail_x: float = TRAIL_X,
    trail_band: float = TRAIL_BAND,
    trail_tau: float = TRAIL_TAU,
) -> pd.DataFrame:
    c = ind["close"]

    # --- peak-aware trailing exit (capture-the-high) ---------------------------
    # The OLD design counted 5 correlated "price-below-a-level" breaches and ramped
    # trend_break toward 100 the DEEPER the decline got — loudest at the bottom, silent
    # at the high (measured corr(score, drawdown-from-1y-high) = -0.55). For a tool whose
    # whole job is to sell NEAR a high, that is inverted: it screamed "SELL" ~12% into a
    # drop, after the high was already gone, and lurched +16 in a day when the correlated
    # breaches fired together. New design: peak sell-pressure on the FRESH roll-over near
    # the high, fade it as the break ages, with a separate non-fading backstop so a slow
    # secular bear still sells instead of holding to the bottom.
    dd = ind["dd_from_high"]
    breach = ((dd - trail_x) / trail_band).clip(0, 1)          # continuous onset over a band -> no cliff
    # break_age = consecutive bars since this breach first opened (the break's OWN age,
    # resets whenever price recovers to breach==0). NOT bars-since-the-literal-high: that
    # pins to the window edge on the rounded/plateau tops typical of THB gold, which would
    # silence the signal at the exact moment it must be loud.
    opened = (breach.to_numpy() > 0).astype(int)
    age = np.zeros(len(opened))
    run = 0
    for i in range(len(opened)):
        run = run + 1 if opened[i] else 0
        age[i] = max(0, run - 1)
    fade = np.exp(-(age / trail_tau))
    fresh = pd.Series(breach.to_numpy() * fade * 100, index=ind.index)   # loud at the fresh break

    # non-fading secular backstop: absolute trend levels (not the fast drawdown, which
    # re-arms downward in a grind) so a sustained bear keeps the tool selling.
    confirm = (
        0.5 * ind["below_200dma"].astype(float)
        + 0.3 * ind["death_cross"].astype(float)
        + 0.2 * ind["below_40w_low"].astype(float)
    ) * 100

    trend_break = (0.70 * fresh + 0.30 * confirm).clip(0, 100)

    # --- overbought stretch (correlated oscillators collapsed via mean) ---
    overbought_raw = pd.concat(
        [
            (ind["stretch_200"] / 0.26).clip(0, 1) * 100,      # 26% above 200-DMA -> 100
            ((ind["rsi14_w"] - 50) / 30).clip(0, 1) * 100,     # weekly RSI 50->0, 80->100
            ((ind["pctb_w"] - 0.5) / 0.5).clip(0, 1) * 100,    # %B 0.5->0, 1.0->100
            (ind["roc252"] / 0.50).clip(0, 1) * 100,           # +50% YoY -> 100
        ],
        axis=1,
    ).mean(axis=1)
    # loud NEAR the high (nudges a trim AT the top); damped once price is deep in a decline
    # so a stale overbought reading from before the drop doesn't keep inflating the score.
    prox = (1.0 - dd / PROX_KNEE).clip(0, 1)
    overbought = overbought_raw * (0.5 + 0.5 * prox)

    # --- momentum rollover (weekly MACD): two INDEPENDENT reads, each 0/50 ---
    # below the signal line (turning down) + below the zero line (confirmed bear territory).
    # (Previously the 2nd term used hist<0, which equals macd<signal — redundant, so momentum
    # was only ever 0 or 100. Using the zero-line makes it graded: 0 / 50 / 100.)
    momentum = (ind["macd_w"] < ind["macd_sig_w"]).astype(float) * 50 + (ind["macd_w"] < 0).astype(float) * 50

    seasonality = _seasonality(c)

    # --- dollar regime (macro): DXY band -> historical sell-pressure for THB gold ---
    if dxy is not None and len(dxy):
        dser = dxy.reindex(ind.index, method="ffill")
        dollar = dser.map(lambda v: DOLLAR_SELL.get(band_of(v), 50.0) if pd.notna(v) else 50.0).astype(float)
    else:
        dollar = pd.Series(50.0, index=ind.index)

    composite = (
        WEIGHTS["trend_break"] * trend_break
        + WEIGHTS["overbought"] * overbought
        + WEIGHTS["momentum"] * momentum
        + WEIGHTS["dollar"] * dollar
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
        & ind["dd_from_high"].notna()
    )

    # 'sell' fires on a FRESH break OR a confirmed bear (not only after a deep death-cross).
    n_trend = (breach > 0).astype(int) + (confirm >= 50).astype(int)   # 0..2

    verdict = np.select(
        [(n_trend >= 2) & (composite >= T_SELL), composite >= T_TRANCHE, composite >= T_TRIM],
        ["sell", "sell_tranche", "trim"],
        default="hold",
    )

    flags = pd.DataFrame(
        {
            "trailing_stop_fired": breach > 0,
            "secular_confirm": confirm >= 50,
            "below_200dma": ind["below_200dma"].astype(bool),
            "death_cross": ind["death_cross"].astype(bool),
            "below_40w_low": ind["below_40w_low"].astype(bool),
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
            "fa_score": dollar.round(2),
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
                "fa_score": _clean(row["fa_score"]),
                "verdict": row["verdict"],
                "active_signals": list(row["active_signals"]),
            }
        )
    for i in range(0, len(records), 1000):
        sb.table("signals_daily").upsert(records[i : i + 1000], on_conflict="trade_date").execute()
    return len(records)
