"""International gold price in THB — the price BASIS the sell-pressure score reads.

The score is computed on the GLOBAL gold price expressed in THB (XAU/USD × USD/THB),
NOT on the Thai association (สมาคมค้าทองคำ) quote. Reason: the association price is a
quantized, locally-lagging derivative of the world price; a purely *local* premium swing
(e.g. the dealer quote catching up to spot) used to jolt the score even when world gold
barely moved. Tracking the world price removes that artifact. Poom still SELLS at the
association bid — that stays the realized/displayed price (see backtest realized price +
the dashboard headline); this module only feeds the indicators.

The conversion matches the web's real-time card EXACTLY, so backfilled history and the
live number sit on one basis:
    THB per 1 baht-weight of 96.5% bar = XAU(USD/oz fine) × USDTHB × (15.244/31.1035) × 0.965

Sources:
  - history (one-time backfill, run from a residential IP): LBMA gold fix (PM, AM
    fallback) for USD/oz + frankfurter.dev (ECB) for USD/THB.
  - ongoing (GitHub cron, NO external call — GTA/XAU APIs 403 datacenters): the phone
    writes goldSpot + bahtPerUSD into gold_price_daily on every sync, so recent days are
    derived straight from there. This keeps the compute-only job self-sufficient.
"""

from __future__ import annotations

import httpx
import pandas as pd

from .load import fetch_macro

LBMA_PM = "https://prices.lbma.org.uk/json/gold_pm.json"
LBMA_AM = "https://prices.lbma.org.uk/json/gold_am.json"
FRANKFURTER = "https://api.frankfurter.dev/v1"
SERIES = "gold_intl_thb"

# identical to web/lib/realtime.ts CONV so history and the live card line up
CONV = (15.244 / 31.1034768) * 0.965  # ≈ 0.47295


def _lbma_usd() -> pd.Series:
    """Daily gold USD/oz (fine): PM fix, AM where PM is missing."""
    headers = {"User-Agent": "Mozilla/5.0"}
    with httpx.Client(timeout=60, headers=headers) as c:
        pm = c.get(LBMA_PM).json()
        am = c.get(LBMA_AM).json()

    def to_ser(rows: list[dict]) -> pd.Series:
        d: dict[pd.Timestamp, float] = {}
        for r in rows:
            v = r.get("v") or []
            if v and v[0] is not None:
                d[pd.Timestamp(r["d"])] = float(v[0])
        return pd.Series(d).sort_index()

    return to_ser(pm).combine_first(to_ser(am)).sort_index()  # prefer PM, fill from AM


def _usdthb(start: str) -> pd.Series:
    """Daily USD/THB from ECB (frankfurter), chunked in 5-year requests like dxy.py."""
    out: dict[pd.Timestamp, float] = {}
    with httpx.Client(timeout=60) as c:
        for y0 in range(int(start[:4]), pd.Timestamp.today().year + 1, 5):
            s = f"{max(int(start[:4]), y0)}-01-01"
            e = f"{y0 + 4}-12-31"
            data = c.get(f"{FRANKFURTER}/{s}..{e}?base=USD&symbols=THB").json().get("rates", {})
            for day, r in data.items():
                if r.get("THB"):
                    out[pd.Timestamp(day)] = float(r["THB"])
    return pd.Series(out).sort_index()


def build_intl_thb(start: str = "2006-01-01") -> pd.Series:
    """International gold in THB/baht-weight (96.5% basis), daily, from LBMA × ECB."""
    usd = _lbma_usd()
    usd = usd[usd.index >= pd.Timestamp(start)]
    fx = _usdthb(start)
    # carry the most recent ECB rate onto each gold-fix date (fix calendars differ slightly)
    fx_on_gold = fx.reindex(usd.index.union(fx.index)).sort_index().ffill().reindex(usd.index)
    intl = (usd * fx_on_gold * CONV).dropna()
    intl.name = SERIES
    return intl


def _upsert(sb, ser: pd.Series, source: str) -> int:
    rows = [
        {"trade_date": d.date().isoformat(), "series": SERIES, "value": round(float(v), 2), "source": source}
        for d, v in ser.items()
        if pd.notna(v)
    ]
    for i in range(0, len(rows), 1000):
        sb.table("macro_daily").upsert(rows[i : i + 1000], on_conflict="trade_date,series").execute()
    return len(rows)


def backfill(sb, start: str = "2006-01-01") -> int:
    """One-time history load into macro_daily(series='gold_intl_thb'). Run locally."""
    return _upsert(sb, build_intl_thb(start), "lbma_x_frankfurter")


def topup_from_daily(sb) -> int:
    """Refresh recent days from the phone's goldSpot×bahtPerUSD in gold_price_daily.

    No external call, so the GitHub compute-only cron stays self-sufficient. These rows
    win over the backfill for the few most-recent days (same basis, just fresher)."""
    rows = (
        sb.table("gold_price_daily")
        .select("trade_date,gold_spot_usd,baht_per_usd")
        .not_.is_("gold_spot_usd", "null")
        .not_.is_("baht_per_usd", "null")
        .order("trade_date")
        .execute()
        .data
    )
    if not rows:
        return 0
    ser = pd.Series(
        {pd.Timestamp(r["trade_date"]): float(r["gold_spot_usd"]) * float(r["baht_per_usd"]) * CONV for r in rows}
    )
    return _upsert(sb, ser, "phone_spot")


def upsert_today(sb, trade_date, spot_usd: float, baht_per_usd: float) -> float:
    """Persist one fresh intl value derived from a live GTA tick's spot/fx. Returns the value."""
    val = float(spot_usd) * float(baht_per_usd) * CONV
    _upsert(sb, pd.Series({pd.Timestamp(trade_date): val}), "gta_tick")
    return val


def load_intl_daily(sb) -> pd.DataFrame:
    """International THB as a daily OHLC frame (O=H=L=C=fix) for indicators.build(..., 0).

    A single daily fix carries no intraday range, so daily H=L=C; weekly high/low come
    from the weekly min/max of the daily fixes. That is all the score needs — it reads
    only closes (and the valid-mask's weekly chandelier/donchian, which the weekly
    min/max satisfy). Pass spread=0: there is no association bid/ask on the world price.
    """
    s = fetch_macro(sb, SERIES)
    df = pd.DataFrame({"trade_date": [d.date() for d in s.index]})
    for col in ("bar_sell_open", "bar_sell_high", "bar_sell_low", "bar_sell_close"):
        df[col] = s.values
    return df


def main() -> None:
    """Local one-time backfill: uv run python -m etl.intl"""
    from . import load

    sb = load.client()
    n_hist = backfill(sb)
    n_recent = topup_from_daily(sb)
    ser = fetch_macro(sb, SERIES)
    print(f"Backfilled {n_hist} history rows + topped up {n_recent} recent rows.")
    print(f"Series span: {ser.index.min().date()} .. {ser.index.max().date()}  ({len(ser)} rows)")
    print(f"Latest intl THB/baht-weight (96.5%): {ser.iloc[-1]:,.0f} on {ser.index[-1].date()}")


if __name__ == "__main__":
    main()
