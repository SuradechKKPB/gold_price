"""US Dollar Index (DXY) reconstruction + conditional gold analysis.

The ICE DXY is a fixed-weight basket of 6 currencies; we reconstruct it from
free ECB FX rates (frankfurter.dev) since no free ICE DXY feed is reachable from
a server. We then study how THB gold behaved over the NEXT 12 months conditioned
on the DXY level bucket (the user's request: avg return, avg loss, return/maxDD).
"""

from __future__ import annotations

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


# Dollar-regime sell-pressure by band, derived from the conditional study (lower
# risk-adjusted forward THB-gold return -> higher sell pressure). Low DXY bands had
# the worst forward returns/ret-per-drawdown; high DXY bands the best (weak-baht tailwind).
DOLLAR_SELL = {"<80": 70.0, "80–90": 58.0, "90–100": 45.0, "100–110": 18.0, ">110": 15.0}


def dollar_regime_score(dxy: float | None) -> float:
    return DOLLAR_SELL.get(band_of(dxy), 50.0) if dxy is not None else 50.0


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


def study(gold_close: pd.Series, dxy: pd.Series) -> dict:
    """Conditional next-12-month THB-gold stats by DXY bucket."""
    g = gold_close.resample("ME").last()
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
    from . import load

    sb = load.client()
    gold = load.fetch_daily(sb)
    gold.index = pd.to_datetime(gold["trade_date"])
    close = gold["bar_sell_close"].astype(float)  # ratio-based study; constant spread is negligible
    dxy = fetch_dxy_series("2006-01-01")
    cur = fetch_current_dxy()
    print(f"Current reconstructed DXY = {cur:.2f}  -> band {band_of(cur)}")
    print(f"DXY span: {dxy.index.min().date()} .. {dxy.index.max().date()}  ({dxy.min():.1f}–{dxy.max():.1f})\n")
    t = study(close, dxy)
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
