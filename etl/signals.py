"""Daily sell-timing score: is today a RICH day to sell into, or a WEAK one?

v4 replaces the trend-break composite (v1-v3). That design was a trailing stop. It was
loudest after price had already fallen, and measured on 2006-2026 its trim+ days filled
3.0% BELOW the centred ±63-day average price (verdict=sell: -4.4%). The sign was negative
in every regime, bull and bear. Its apparent parity with DCA in the old backtest came from
holding longer through a bull market, not from picking days. See HANDOFF §3.

The score is now a percentile of strength, built from two reads that flagged richer-than-
surrounding sale days in all three regimes (2006-11 bull, 2011-18 bear, 2019-26 bull):

  rich = mean( pct_rank(price / SMA50 - 1), pct_rank(price / 40-bar low - 1) )   0..100

Each rank is POINT-IN-TIME: at date t it compares today's value only with the ~3 years of
values up to t, so no score has seen the future distribution. 100 means stronger than every
day in that window.

The BRAKE is the old trailing stop with its sign corrected. A drop from the recent high no
longer says "sell". It says "not today": a tranche that falls due on a weak day may wait
(etl/plan.py bounds how long). The band is in units of one-month volatility, not a fixed 3%,
because 3% was 3.3 sigma of daily noise in 2006-19 and only 2.0 sigma in 2026.

PACE is not decided here. How much to sell and by when is the seller's plan (etl/plan.py);
this module only grades the day. Weekly MACD, the DXY band and seasonality are gone from the
score: MACD flipped from bearish to bullish ON the August 2026 high and cut 10 points as
price peaked, and none of the three earned its weight in the per-regime tests.

signals_daily keeps its v3 columns (no migration), with this mapping:
  sell_pressure -> rich (the headline 0-100)
  overbought    -> percentile of price vs SMA50
  momentum      -> percentile of the rally from the 40-bar low
  trend_break   -> brake depth: drawdown as % of the brake band (100 = at or past it)
  seasonality, fa_score -> NULL (retired)

CALIBRATION: thresholds and the brake multiple were chosen by etl/backtest.py on pre-2020
campaigns only, then reported on 2020+ and per regime. The two inputs were chosen after
looking at the full history (2026-10-02 audit). The numbers are therefore an upper bound,
not an expectation.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

# Bump whenever ANY formula/constant below changes. compute.py compares this to the version
# last written and, on a mismatch, rewrites the ENTIRE signals_daily history.
SCORE_VERSION = 4

RANK_MIN = 252          # a year of history before a percentile means anything
# Percentile over the last ~3 years rather than all history. Chosen on pre-2020 campaigns
# over expanding; it also adapts to a volatility regime, which an all-history rank cannot
# (2026's 24% vol would otherwise read as "rich" for weeks on end).
RANK_WINDOW: int | None = 756
RICH = 80.0             # upper fifth of the last 3 years: worth watching
VERY_RICH = 90.0        # upper tenth: the plan sells a tranche early (plan.Rules.trigger)
HYSTERESIS = 5.0        # a tier is left only when rich falls this far below its entry line
BRAKE_K = 1.0           # brake band = BRAKE_K x one-month sigma of the price
BRAKE_FLOOR, BRAKE_CAP = 0.03, 0.12
BRAKE_RICH_MAX = 50.0   # a dip that still ranks in the upper half is not a weak day

VERDICTS = ("weak", "neutral", "rich", "very_rich")


def pit_rank(s: pd.Series, window: int | None = RANK_WINDOW) -> pd.Series:
    """Point-in-time percentile (0-100) of each value among the values up to that date."""
    roll = s.expanding(min_periods=RANK_MIN) if window is None else s.rolling(window, min_periods=RANK_MIN)
    return roll.rank(pct=True) * 100.0


def brake_band(vol_m: pd.Series, k: float = BRAKE_K) -> pd.Series:
    """Drawdown from the 40-bar high at which the brake engages, as a fraction."""
    return (k * vol_m).clip(BRAKE_FLOOR, BRAKE_CAP)


def _verdict(rich: np.ndarray, brake: np.ndarray, rich_t: float, very_t: float, margin: float) -> np.ndarray:
    """weak / neutral / rich / very_rich, with a deadband on the way down.

    A tier is entered when `rich` crosses its line and left only when it falls `margin`
    below it, so day-to-day noise around 80 cannot flip the verdict (and spend a LINE
    alert) every other day. The brake overrides: a weak day is weak whatever came before.
    """
    lines = (rich_t, very_t)
    out = np.empty(len(rich), dtype=object)
    cur = 0  # 0 neutral, 1 rich, 2 very_rich
    for i, x in enumerate(rich):
        if brake[i]:
            cur = 0
            out[i] = "weak"
            continue
        if np.isnan(x):
            cur = 0
            out[i] = "neutral"
            continue
        while cur > 0 and x < lines[cur - 1] - margin:
            cur -= 1
        enter = 2 if x >= very_t else 1 if x >= rich_t else 0
        cur = max(cur, enter)
        out[i] = VERDICTS[cur + 1]
    return out


def compute_scores(
    ind: pd.DataFrame,
    *,
    rank_window: int | None = RANK_WINDOW,
    brake_k: float = BRAKE_K,
    rich_t: float = RICH,
    very_t: float = VERY_RICH,
) -> pd.DataFrame:
    ext_r = pit_rank(ind["ext50"], rank_window)
    rally_r = pit_rank(ind["rally_from_low"], rank_window)
    rich = (ext_r + rally_r) / 2.0

    band = brake_band(ind["vol_m"], brake_k)
    dd = ind["dd_from_high"]
    brake = (dd >= band) & (rich < BRAKE_RICH_MAX)
    brake_depth = (dd / band).clip(0, 1) * 100.0

    verdict = _verdict(rich.to_numpy(), brake.to_numpy(), rich_t, very_t, HYSTERESIS)

    flags = pd.DataFrame(
        {
            "brake": brake,
            "stretched_above_sma50": ext_r >= VERY_RICH,
            "sharp_rally_from_low": rally_r >= VERY_RICH,
            "at_recent_high": dd <= 0,
        }
    )
    active = flags.apply(lambda r: [k for k, v in r.items() if bool(v)], axis=1)

    res = pd.DataFrame(
        {
            "sell_pressure": rich.round(2),
            "trend_break": brake_depth.round(2),
            "overbought": ext_r.round(2),
            "momentum": rally_r.round(2),
            "verdict": pd.Series(verdict, index=ind.index),
            "brake": brake,
            "brake_dd": band,
            "active_signals": active,
        }
    )
    valid = rich.notna() & dd.notna() & ind["vol_m"].notna()
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
                "seasonality": None,   # retired in v4
                "fa_score": None,      # retired in v4 (the DXY band)
                "verdict": row["verdict"],
                "active_signals": list(row["active_signals"]),
            }
        )
    for i in range(0, len(records), 1000):
        sb.table("signals_daily").upsert(records[i : i + 1000], on_conflict="trade_date").execute()
    return len(records)


def prune_signals(sb, scores: pd.DataFrame) -> int:
    """Delete signals_daily rows the current formula did not produce. Full rewrites only.

    SCORE_VERSION exists so the backtest never calibrates on a mixed-formula series, but
    upsert alone could not deliver that: a full rewrite overwrote the dates the current
    basis covers and left every other date untouched. Rows from the retired
    association-price epoch therefore survived indefinitely — 1,159 of them when this was
    found, 1,015 on weekends (the association quotes some Saturdays, LBMA never fixes),
    and 1,156 of the backtest's 6,199 price days were reading one. Roughly a fifth of the
    scored history the harness measured belonged to a formula that no longer exists.

    A row with no basis in the current series cannot be recomputed and is not evidence of
    anything, so removing it loses nothing. Called only on the full-rewrite path, where
    `scores` is by definition the complete current history.
    """
    keep = {idx.date().isoformat() for idx in scores.index}
    have: list[str] = []
    page = 0
    while True:
        res = sb.table("signals_daily").select("trade_date").order("trade_date").range(page * 1000, page * 1000 + 999).execute()
        have.extend(r["trade_date"] for r in res.data)
        if len(res.data) < 1000:
            break
        page += 1
    stale = sorted(set(have) - keep)
    for i in range(0, len(stale), 200):
        sb.table("signals_daily").delete().in_("trade_date", stale[i : i + 200]).execute()
    return len(stale)
