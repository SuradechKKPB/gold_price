"""Technical indicators for sell-timing, computed in pandas (no TA-Lib).

Horizon is 3-12 months, so trend/momentum indicators run on WEEKLY bars and are
forward-filled back to daily. Everything is computed on the seller-relevant
buy-in basis (bar sell-out minus the spread); the constant shift leaves all
comparative signals unchanged but keeps overlay levels aligned to the bid price.
"""

from __future__ import annotations

import pandas as pd


def _wilder(series: pd.Series, period: int) -> pd.Series:
    """Wilder smoothing via EWM (alpha = 1/period)."""
    return series.ewm(alpha=1 / period, adjust=False).mean()


def wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    rs = _wilder(gain, period) / _wilder(loss, period)
    return 100 - 100 / (1 + rs)


def build(daily: pd.DataFrame, spread_thb: float) -> pd.DataFrame:
    """Return a daily-indexed frame of indicators on the buy-in basis.

    `daily` has columns trade_date, bar_sell_open/high/low/close.
    """
    df = daily.copy()
    df.index = pd.to_datetime(df["trade_date"])
    o = df["bar_sell_open"] - spread_thb
    h = df["bar_sell_high"] - spread_thb
    low = df["bar_sell_low"] - spread_thb
    c = df["bar_sell_close"] - spread_thb

    out = pd.DataFrame(index=df.index)
    out["close"] = c

    # --- daily-basis trend signals ---
    sma50 = c.rolling(50).mean()
    sma200 = c.rolling(200).mean()
    out["sma200"] = sma200
    out["stretch_200"] = c / sma200 - 1.0          # % above the 200-DMA (Mayer-style)
    out["death_cross"] = sma50 < sma200
    out["below_200dma"] = c < sma200
    out["roc252"] = c.pct_change(252)              # annual momentum

    # --- weekly-basis trend/momentum signals (W-FRI), ffilled to daily ---
    w = pd.DataFrame(
        {
            "high": h.resample("W-FRI").max(),
            "low": low.resample("W-FRI").min(),
            "close": c.resample("W-FRI").last(),
        }
    ).dropna()

    wk = pd.DataFrame(index=w.index)
    wk["rsi14_w"] = wilder_rsi(w["close"], 14)

    macd_line = w["close"].ewm(span=12, adjust=False).mean() - w["close"].ewm(span=26, adjust=False).mean()
    macd_sig = macd_line.ewm(span=9, adjust=False).mean()
    wk["macd_w"] = macd_line
    wk["macd_sig_w"] = macd_sig
    wk["macd_hist_w"] = macd_line - macd_sig

    tr = pd.concat(
        [w["high"] - w["low"], (w["high"] - w["close"].shift()).abs(), (w["low"] - w["close"].shift()).abs()],
        axis=1,
    ).max(axis=1)
    atr22 = _wilder(tr, 22)
    wk["chandelier_w"] = w["high"].rolling(22).max() - 3 * atr22   # Chandelier Exit (22, 3)
    wk["donchian_low_10w"] = w["low"].rolling(10).min()
    wk["donchian_low_20w"] = w["low"].rolling(20).min()

    bb_mid = w["close"].rolling(20).mean()
    bb_sd = w["close"].rolling(20).std(ddof=0)  # population std = classic Bollinger
    pctb = (w["close"] - (bb_mid - 2 * bb_sd)) / (4 * bb_sd)
    pctb[bb_sd == 0] = 0.5  # flat 20-week window -> mid-band (avoid inf/NaN into overbought)
    wk["pctb_w"] = pctb

    out = out.join(wk.reindex(out.index, method="ffill"))
    return out
