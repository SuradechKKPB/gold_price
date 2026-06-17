"""Idempotent UPSERTs into Supabase (service-role; bypasses RLS)."""

from __future__ import annotations

import pandas as pd
from supabase import Client, create_client

from .config import settings
from .gta import GoldTick


def client() -> Client:
    if not settings.has_supabase:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set")
    return create_client(settings.supabase_url, settings.supabase_service_role_key)


def fetch_daily(sb: Client) -> pd.DataFrame:
    """Load the full daily bar-sell history from Supabase (source of truth)."""
    rows: list[dict] = []
    page = 0
    cols = "trade_date,bar_sell_open,bar_sell_high,bar_sell_low,bar_sell_close"
    while True:
        res = sb.table("gold_price_daily").select(cols).order("trade_date").range(page * 1000, page * 1000 + 999).execute()
        rows.extend(res.data)
        if len(res.data) < 1000:
            break
        page += 1
    df = pd.DataFrame(rows)
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    for c in ["bar_sell_open", "bar_sell_high", "bar_sell_low", "bar_sell_close"]:
        df[c] = df[c].astype(float)
    return df


def upsert_tick(sb: Client, tick: GoldTick) -> None:
    sb.table("gold_price_ticks").upsert(
        {
            "as_time": f"{tick.as_time}+07:00",  # gta asTime is bangkok wall-clock
            "seq": tick.seq,
            "bar_buy": tick.bar_buy,
            "bar_sell": tick.bar_sell,
            "ornament_buy": tick.ornament_buy,
            "gold9999_buy": tick.gold9999_buy,
            "gold_spot_usd": tick.gold_spot_usd,
            "baht_per_usd": tick.baht_per_usd,
            "chg_prev_row": tick.chg_prev_row,
            "chg_prev_day": tick.chg_prev_day,
            "gold_price_id": tick.gold_price_id,
        },
        on_conflict="as_time,seq",
    ).execute()


def upsert_daily(sb: Client, daily: pd.DataFrame, spread_thb: float) -> int:
    """Write the daily bar-sell series; derive a modeled buy-in close = sell - spread."""
    records = []
    for row in daily.itertuples(index=False):
        records.append(
            {
                "trade_date": row.trade_date.isoformat(),
                "bar_sell_open": float(row.bar_sell_open),
                "bar_sell_high": float(row.bar_sell_high),
                "bar_sell_low": float(row.bar_sell_low),
                "bar_sell_close": float(row.bar_sell_close),
                "bar_buy_close": float(row.bar_sell_close) - spread_thb,
                "source": "gta_ohlc",
            }
        )
    # chunk to stay well under payload limits
    for i in range(0, len(records), 1000):
        sb.table("gold_price_daily").upsert(records[i : i + 1000], on_conflict="trade_date").execute()
    return len(records)
