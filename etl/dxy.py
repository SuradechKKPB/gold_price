"""US Dollar Index (DXY) reconstruction + conditional gold analysis.

The ICE DXY is a fixed-weight basket of 6 currencies; we reconstruct it from
free ECB FX rates (frankfurter.dev) since no free ICE DXY feed is reachable from
a server. We then study how THB gold behaved over the NEXT 12 months conditioned
on the DXY level bucket (the user's request: avg return, avg loss, return/maxDD).
"""

from __future__ import annotations

import math

import httpx
import numpy as np
import pandas as pd

FRANKFURTER = "https://api.frankfurter.dev/v1"
CCYS = "EUR,JPY,GBP,CAD,SEK,CHF"
BANDS = [(-np.inf, 80, "<80"), (80, 90, "80–90"), (90, 100, "90–100"), (100, 110, "100–110"), (110, np.inf, ">110")]


def band_of(dxy: float) -> str:
    for lo, hi, label in BANDS:
        if lo <= dxy < hi:
            return label
    return ">110"


# Dollar-regime sell-pressure by band. MONOTONE INCREASING in the dollar: a strong/
# rising USD is a headwind for gold, so a higher DXY leans toward selling.
#
# This REVERSES an earlier full-sample table (<80:70 ... 100–110:18) that had it
# backwards — that table was fitted on 2006–2026 including the 2020–2026 window where
# a high DXY *coincided* with a THB-gold melt-up, so it "learned" high-dollar = don't-
# sell and pinned the fundamental sub-score at 18 straight through the rally (look-ahead:
# the backtest then scored the very windows the table was fitted on). Re-running the
# conditional study on PRE-2020 data only tells the opposite, economically sensible
# story — DXY 100–110 preceded the weakest forward returns (avg +0.3%, 33% positive)
# and <80 the best (+7.5%, 70% positive). The mapping below is anchored to that clean
# pre-2020 ranking (thin above 100, so rounded to a defensible monotone prior, not
# over-fitted to n=6). See dxy.study(gold, dxy, end='2020-01-01') to reproduce.
DOLLAR_SELL = {"<80": 30.0, "80–90": 40.0, "90–100": 55.0, "100–110": 68.0, ">110": 75.0}


def dollar_regime_score(dxy: float | None) -> float:
    """Map a DXY level to sell-pressure. Missing/NaN → neutral 50 — never the >110
    high-sell band, which band_of() would otherwise return for a NaN (all comparisons
    False → fall-through). signals.compute_scores already guards this inline; this keeps
    the standalone entry point safe too."""
    if dxy is None or not math.isfinite(dxy):
        return 50.0
    return DOLLAR_SELL.get(band_of(dxy), 50.0)


def backfill_macro(sb) -> int:
    """Write the reconstructed daily DXY into macro_daily(series='dxy')."""
    ser = fetch_dxy_series("2006-01-01")
    rows = [
        {"trade_date": d.date().isoformat(), "series": "dxy", "value": round(float(v), 2), "source": "frankfurter"}
        for d, v in ser.items()
    ]
    for i in range(0, len(rows), 1000):
        sb.table("macro_daily").upsert(rows[i : i + 1000], on_conflict="trade_date,series").execute()
    return len(rows)


def topup(sb, days: int = 60) -> int:
    """Refresh only the RECENT tail of macro_daily(series='dxy'). Returns rows written.

    backfill_macro() rewrites all of 2006-today on every call, which is far too heavy to
    hang off the daily cron. But leaving the series unrefreshed is worse: signals ffills
    the last stored DXY forward, so a stale series pins the dollar sub-score (12% of the
    composite) at whatever band the dollar was in weeks ago, silently and with no error.
    This fetches one short window instead, which is cheap enough to run every day.

    `days` covers the gap with slack: frankfurter only publishes ECB business days, so a
    long holiday plus a weekend can leave the tail several days short of today, and the
    window has to reach back past that to land on real data. Overlap is free — the upsert
    is idempotent on (trade_date, series).

    Never raises. A frankfurter outage must not take down the scoring run: the score
    still computes off the stored series, one band-crossing stale at worst.
    """
    end = pd.Timestamp.today().normalize()
    start = end - pd.Timedelta(days=days)
    try:
        with httpx.Client(timeout=20) as c:
            url = f"{FRANKFURTER}/{start.date()}..{end.date()}?base=USD&symbols={CCYS}"
            data = c.get(url).raise_for_status().json().get("rates", {})
    except (httpx.HTTPError, ValueError) as e:
        print(f"dxy.topup: frankfurter unreachable ({e.__class__.__name__}); keeping the stored series.")
        return 0

    rows = [
        {"trade_date": day, "series": "dxy", "value": round(v, 2), "source": "frankfurter"}
        for day, r in sorted(data.items())
        if (v := _dxy_from_rates(r)) is not None
    ]
    if not rows:
        print(f"dxy.topup: no ECB rates in {start.date()}..{end.date()}; keeping the stored series.")
        return 0

    sb.table("macro_daily").upsert(rows, on_conflict="trade_date,series").execute()
    last = rows[-1]
    print(f"dxy.topup: {len(rows)} rows through {last['trade_date']} (DXY {last['value']:.2f} -> band {band_of(last['value'])}).")
    return len(rows)


def _dxy_from_rates(r: dict) -> float | None:
    try:
        eurusd, gbpusd = 1 / r["EUR"], 1 / r["GBP"]
        return (
            50.14348112
            * eurusd ** -0.576
            * r["JPY"] ** 0.136
            * gbpusd ** -0.119
            * r["CAD"] ** 0.091
            * r["SEK"] ** 0.042
            * r["CHF"] ** 0.036
        )
    except (KeyError, TypeError, ZeroDivisionError):
        return None


def fetch_dxy_series(start: str = "2006-01-01") -> pd.Series:
    """Daily reconstructed DXY from ECB FX (chunked 5-year requests)."""
    out: dict[str, float] = {}
    years = list(range(int(start[:4]), pd.Timestamp.today().year + 1, 5))
    with httpx.Client(timeout=40) as c:
        for y in years:
            s = f"{max(int(start[:4]), y)}-01-01" if y == years[0] else f"{y}-01-01"
            e = f"{y + 4}-12-31"
            data = c.get(f"{FRANKFURTER}/{s}..{e}?base=USD&symbols={CCYS}").json().get("rates", {})
            for day, r in data.items():
                v = _dxy_from_rates(r)
                if v is not None:
                    out[day] = v
    ser = pd.Series(out)
    ser.index = pd.to_datetime(ser.index)
    return ser.sort_index()


def fetch_current_dxy() -> float | None:
    with httpx.Client(timeout=20) as c:
        r = c.get(f"{FRANKFURTER}/latest?base=USD&symbols={CCYS}").json().get("rates", {})
    return _dxy_from_rates(r)


def study(gold_close: pd.Series, dxy: pd.Series, end: str | None = None) -> dict:
    """Conditional next-12-month THB-gold stats by DXY bucket.

    `end` (e.g. '2020-01-01') keeps the ENTIRE conditioning-plus-forward window before
    the cutoff — the clean, no-look-ahead sample the deployed DOLLAR_SELL table is anchored
    to. The 2020–26 THB-gold melt-up (where a high DXY coincided with rising gold) is
    excluded outright; gating only the start date would leak it back in and break the
    monotone ranking, so the whole series is truncated.
    """
    g = gold_close.resample("ME").last()
    if end is not None:
        g = g[g.index < pd.Timestamp(end)]
    d = dxy.resample("ME").last().reindex(g.index, method="ffill")
    rows = []
    vals = g.values
    for i in range(len(g) - 12):
        if np.isnan(d.iloc[i]):
            continue
        path = vals[i : i + 13]  # start + next 12 months
        ret12 = path[-1] / path[0] - 1
        peak = np.maximum.accumulate(path)
        maxdd = float((path / peak - 1).min())
        rows.append((band_of(d.iloc[i]), ret12, maxdd))
    df = pd.DataFrame(rows, columns=["band", "ret12", "maxdd"])
    table = {}
    for _, _, label in BANDS:
        b = df[df["band"] == label]
        if len(b) == 0:
            table[label] = {"n": 0}
            continue
        avg_ret = float(b["ret12"].mean())
        losses = b.loc[b["ret12"] < 0, "ret12"]
        avg_loss = float(losses.mean()) if len(losses) else 0.0
        mean_dd = float(b["maxdd"].mean())
        table[label] = {
            "n": int(len(b)),
            "avg_ret": round(avg_ret * 100, 1),
            "avg_loss": round(avg_loss * 100, 1),
            "pos_pct": round(float((b["ret12"] > 0).mean()) * 100, 0),
            "ret_dd": round(avg_ret / abs(mean_dd), 2) if mean_dd < 0 else None,
        }
    return table


def main() -> None:
    """Reproduce the DOLLAR_SELL anchor table: pre-2020 conditional forward returns of
    the INTERNATIONAL THB basis (the basis the score actually reads) by DXY band, using
    the stored DXY the score joins. Run locally: uv run python -m etl.dxy"""
    from . import load

    sb = load.client()
    gold = load.fetch_macro(sb, "gold_intl_thb")   # the score's basis, not the association quote
    dxy = load.fetch_macro(sb, "dxy")              # the stored DXY the score joins
    cur = fetch_current_dxy()
    if cur is not None:
        print(f"Current reconstructed DXY = {cur:.2f}  -> band {band_of(cur)}")
    print(f"DXY span: {dxy.index.min().date()} .. {dxy.index.max().date()}  ({dxy.min():.1f}–{dxy.max():.1f})")
    print("Sample: entire conditioning+forward window pre-2020 (clean / no look-ahead) — anchors DOLLAR_SELL.\n")
    t = study(gold, dxy, end="2020-01-01")
    print(f"{'band':>10} | {'n':>4} | {'avg 12m ret':>12} | {'avg loss':>9} | {'%pos':>5} | {'ret/maxDD':>9}")
    print("-" * 66)
    for _, _, label in BANDS:
        r = t[label]
        if r["n"] == 0:
            print(f"{label:>10} | {0:>4} | (no samples)")
            continue
        print(f"{label:>10} | {r['n']:>4} | {r['avg_ret']:>11}% | {r['avg_loss']:>8}% | {r['pos_pct']:>4.0f}% | {str(r['ret_dd']):>9}")


if __name__ == "__main__":
    main()
