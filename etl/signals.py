"""Composite 0-100 sell-pressure score + verdict.

Trend-break-weighted by design: gold is in a strong secular uptrend where
overbought/mean-reversion signals fire too early, so trailing-stop / trend-break
exits dominate the score and correlated oscillators are collapsed into one
overbought sub-score (not triple-counted).

CALIBRATION CAVEAT: the weights and the 44/52/60 cut-offs were chosen by inspecting the
full 2006-2026 series, so they carry human look-ahead that no in-sample/out-of-sample
split can undo. The mechanics below are strictly causal (verified by shock injection: no
sub-score moves on a date before the shock), but the CONSTANTS have seen the whole tape.
Treat any backtested edge as an upper bound.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .dxy import DOLLAR_SELL, band_of

# Bump whenever ANY scoring formula/constant below changes. compute.py compares this
# to the version last written to signals_daily and, on a mismatch, rewrites the ENTIRE
# history (not just tail-30) so the backtest never calibrates on a mixed-formula series.
SCORE_VERSION = 3

WEIGHTS = {"trend_break": 0.40, "overbought": 0.25, "momentum": 0.18, "dollar": 0.12, "seasonality": 0.05}

# Peak-aware trailing-exit knobs (calibrated against capture-the-high in backtest.py).
TRAIL_X = 0.03      # a break "opens" once price is 3% below its recent high
TRAIL_BAND = 0.05   # breach saturates over the next 5% (3% -> 0, >=8% -> 1): continuous, no cliff
TRAIL_TAU = 20.0    # freshness half-life-ish in bars: a break fades as IT ages (~4 weeks)
PROX_KNEE = 0.06    # overbought is "near the high" within this drawdown, damped beyond it
HYSTERESIS = 2.5    # verdict deadband (composite pts): sticky on the way down, no flip-flop

# Verdict cut-offs for the peak-aware composite (price BASIS = international THB; see
# etl/intl.py). Set from percentiles of the CLEAN score distribution (no look-ahead
# COMPONENTS): trim ~p89, tranche ~p95, sell ~p99 (score max ~68). A laddered exit at
# these levels fires on ~11% of days and 'sell' still requires n_trend>=2 (fresh break
# AND confirmed bear).
#
# Those percentiles are full-sample, so these three numbers are the single largest piece
# of human look-ahead in the model — and backtest.LADDER_GRID then searches a grid
# centred on them, which cannot un-see it.
#
# What the harness can actually say: on T+1 fills with pre-2020 selection the ladder beat
# a plain DCA-out in ~54% of windows, but those windows overlap ~99% and the history holds
# only ~17 INDEPENDENT 12-month windows (backtest.n_eff). At n=17 that win rate carries a
# binomial CI of roughly 0.33-0.77. There is no measurable edge over DCA-out in either
# direction. Use this ladder as a DCA backbone with signal acceleration, never as a
# top-picker. Realised price is the association bid; see backtest.py.
T_TRIM, T_TRANCHE, T_SELL = 44.0, 52.0, 60.0


def _seasonality(close: pd.Series, min_years: int = 3) -> pd.Series:
    """Point-in-time month tilt: historically weak months -> higher sell pressure.

    POINT-IN-TIME by construction: at any date the weak/strong ranking is estimated
    ONLY from monthly returns realized up to that date (expanding), so a historical
    score never 'knows' the future full-sample average of its calendar month — the
    look-ahead that inflated every backtested score. Neutral (50) until a month has
    >= min_years observations and at least two months qualify."""
    import bisect

    m = close.resample("ME").last().pct_change().dropna()
    buckets: dict[int, list[float]] = {}
    taus: list[pd.Timestamp] = []
    vectors: list[dict[int, float]] = []
    for tau, ret in m.items():
        buckets.setdefault(tau.month, []).append(float(ret))
        means = {mo: sum(v) / len(v) for mo, v in buckets.items() if len(v) >= min_years}
        if len(means) >= 2:
            lo, hi = min(means.values()), max(means.values())
            span = hi - lo
            scaled = {mo: (50.0 if span <= 0 else (hi - mu) / span * 100.0) for mo, mu in means.items()}
        else:
            scaled = {}
        taus.append(tau)
        vectors.append(scaled)

    out = pd.Series(50.0, index=close.index)
    for d in close.index:
        i = bisect.bisect_right(taus, d) - 1  # latest month-end whose data is fully known by d
        if i >= 0:
            out.loc[d] = vectors[i].get(d.month, 50.0)
    return out


_TIER_NAME = ["hold", "trim", "sell_tranche", "sell"]


def _hysteretic_verdict(composite: pd.Series, n_trend: pd.Series, margin: float = HYSTERESIS) -> np.ndarray:
    """Map the composite to a verdict tier with a hysteresis deadband so day-to-day noise
    around a threshold can't flip-flop the verdict (observed hold<->trim churn). A tier is
    ENTERED when the composite crosses its threshold, but only EXITED when the composite
    falls `margin` points back below it — sticky on the way down. 'sell' additionally
    requires the n_trend>=2 gate, which is hard (dropping it steps straight to tranche)."""
    thr = [T_TRIM, T_TRANCHE, T_SELL]  # thresholds to reach tiers 1,2,3
    comp = composite.to_numpy()
    gate = (n_trend.to_numpy() >= 2)
    out = np.empty(len(comp), dtype=object)
    cur = 0
    for i in range(len(comp)):
        x = comp[i]
        # exit: step down while we're a margin below the current tier's entry threshold
        while cur > 0 and x < thr[cur - 1] - margin:
            cur -= 1
        # 'sell' gate is a hard requirement, not a hysteresis band
        if cur == 3 and not gate[i]:
            cur = 2
        # enter: raise to the highest tier whose entry condition holds now
        enter = 0
        if x >= thr[0]:
            enter = 1
        if x >= thr[1]:
            enter = 2
        if x >= thr[2] and gate[i]:
            enter = 3
        cur = max(cur, enter)
        out[i] = _TIER_NAME[cur]
    return out


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
    # break_age fades an AGING break so the score is loudest on a FRESH roll-over near the
    # high. The clock restarts on every fresh deterioration (breach rising vs the prior
    # bar), not just when price fully recovers — so a SECOND leg down from a lower high
    # (the last good exit before a deeper decline) re-arms loud instead of arriving pre-
    # faded. During a stall/partial rally breach flattens or falls and the break ages,
    # correctly quietening.
    b = breach.to_numpy()
    age = np.zeros(len(b))
    run = 0
    for i in range(len(b)):
        fresh_leg = b[i] > 0 and (i == 0 or b[i] > b[i - 1] + 1e-9)  # new/deeper break -> reset
        if b[i] <= 0 or fresh_leg:
            run = 0
        else:
            run += 1
        age[i] = run
    fade = np.exp(-(age / trail_tau))
    fresh = pd.Series(b * fade * 100, index=ind.index)   # loud at each fresh break

    # non-fading secular backstop: absolute trend levels (not the fast drawdown, which
    # re-arms downward in a grind) so a sustained bear keeps the tool selling. GRADED &
    # PRICE-CONFIRMED, not lagging binaries: the 200-DMA leg ramps with distance below the
    # average, and the regime (SMA50<SMA200) leg ramps with the SMA spread but is GATED on
    # price<SMA50 — so a death cross that prints purely from 50-day-old data rolling off
    # cannot escalate sell-pressure while price is rallying back above the fast average.
    c200, sma200, sma50 = c, ind["sma200"], ind["sma50"]
    d200 = ((sma200 - c200) / (0.03 * sma200)).clip(0, 1)                       # 0 at MA, 1 at -3%
    dregime = ((sma200 - sma50) / (0.02 * sma200)).clip(0, 1) * (c200 < sma50)  # gated on price<SMA50
    confirm = (
        0.5 * d200
        + 0.3 * dregime
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

    # --- momentum rollover (weekly MACD): CONTINUOUS, two independent reads ---
    # Depth below the signal line (turning down) + depth below the zero line (confirmed
    # bear territory), each 0..50, normalised by the point-in-time mean-abs scale of the
    # relevant MACD quantity so the reads are graded, not a 0/50/100 step. The old step
    # jumped +9 composite points overnight the instant MACD crossed a line, then pinned at
    # 100 for weeks (zero marginal information); the continuous form ramps with how far the
    # roll-over has actually progressed and eases as it recovers.
    macd, sig = ind["macd_w"], ind["macd_sig_w"]
    s_hist = (macd - sig).abs().expanding(min_periods=52).mean().replace(0, np.nan)
    s_macd = macd.abs().expanding(min_periods=52).mean().replace(0, np.nan)
    below_sig = ((sig - macd) / s_hist).clip(0, 1) * 50
    below_zero = ((-macd) / s_macd).clip(0, 1) * 50
    step_fallback = (macd < sig).astype(float) * 50 + (macd < 0).astype(float) * 50  # warm-up only
    momentum = (below_sig + below_zero).fillna(step_fallback)

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

    verdict = _hysteretic_verdict(composite, n_trend)

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
