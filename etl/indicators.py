"""Daily indicators the sell-timing score reads, computed in pandas (no TA-Lib).

Every column is causal: a value at date t uses closes up to and including t only.

The score asks one question of the tape — is today a RICH day to sell into, or a WEAK one?
(see etl/signals.py). Two measurements carry that, both chosen because they flagged richer-
than-surrounding sale days in all three regimes of the 2006-2026 history, bull and bear
alike: how far price stands above its 50-day average, and how far it has run up from its
40-bar low. The drawdown from the 40-bar high feeds the brake; the one-month volatility
scales the brake so a 3% dip is not treated the same in a 14%-vol year and a 24%-vol one.

`spread_thb` shifts the input onto a chosen price basis. Callers pass 0: the score reads the
INTERNATIONAL THB series, which has no association bid/ask to subtract.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

LB = 40          # ~8 trading weeks: the window the recent high and low are taken over
VOL_WIN = 60     # trading days behind the volatility estimate


def build(daily: pd.DataFrame, spread_thb: float) -> pd.DataFrame:
    """Return a daily-indexed frame of indicators. `daily` has trade_date and bar_sell_close."""
    df = daily.copy()
    df.index = pd.to_datetime(df["trade_date"])
    c = df["bar_sell_close"] - spread_thb

    out = pd.DataFrame(index=df.index)
    out["close"] = c
    sma50 = c.rolling(50).mean()
    out["sma50"] = sma50
    out["sma200"] = c.rolling(200).mean()

    # Strength: the two sell-into-strength reads.
    out["ext50"] = c / sma50 - 1.0
    recent_low = c.rolling(LB, min_periods=20).min()
    out["recent_low"] = recent_low
    out["rally_from_low"] = c / recent_low - 1.0

    # Weakness: distance below the recent high, which the brake measures against.
    recent_high = c.rolling(LB, min_periods=20).max()
    out["recent_high"] = recent_high
    out["dd_from_high"] = ((recent_high - c) / recent_high).clip(lower=0)

    # One-month sigma of log returns, so a brake band can be stated in units of the noise.
    out["vol_m"] = np.log(c).diff().rolling(VOL_WIN).std() * np.sqrt(21)
    return out
