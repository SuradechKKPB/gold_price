"""Gold Traders Association of Thailand (สมาคมค้าทองคำ) data client.

GTA endpoints are free JSON but Cloudflare-fronted and block many datacenter IPs
(e.g. GitHub Actions) with 403. So we send full browser headers and fall back to
the thaigold.info mirror (plain host) for the live tick when GTA refuses.

We sell at the buy-in (bid). /ohlc is the bar sell-out history (used for the
one-time backfill); the nightly job only needs today's tick — history lives in
Supabase.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from tenacity import retry, stop_after_attempt, wait_exponential

BASE = "https://www.goldtraders.or.th/api/GoldPrices"
THAIGOLD = "http://www.thaigold.info/RealTimeDataV2/gtdata_.json"
BANGKOK = ZoneInfo("Asia/Bangkok")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "th,en-US;q=0.9,en;q=0.8",
    "Referer": "https://www.goldtraders.or.th/",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}


class GoldTick(BaseModel):
    """One GTA round snapshot."""

    model_config = ConfigDict(populate_by_name=True)

    gold_price_id: int = Field(default=0, alias="goldPriceID")
    as_time: str = Field(alias="asTime")  # bangkok wall-clock, e.g. 2026-06-17T12:58:00
    seq: int = Field(default=0, alias="seq")
    bar_buy: float | None = Field(default=None, alias="bL_BuyPrice")
    bar_sell: float | None = Field(default=None, alias="bL_SellPrice")
    ornament_buy: float | None = Field(default=None, alias="oM965_BuyPrice")
    gold9999_buy: float | None = Field(default=None, alias="oM9999_BuyPrice")
    gold_spot_usd: float | None = Field(default=None, alias="goldSpot")
    baht_per_usd: float | None = Field(default=None, alias="bahtPerUSD")
    chg_prev_row: float | None = Field(default=None, alias="priceChangeFromPrevRow")
    chg_prev_day: float | None = Field(default=None, alias="priceChangeFromPrevDayLast")


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=2, max=20))
def _get(url: str) -> object:
    with httpx.Client(timeout=30, headers=HEADERS, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.json()


def fetch_latest() -> GoldTick:
    return GoldTick.model_validate(_get(f"{BASE}/Latest"))


def fetch_ohlc() -> pd.DataFrame:
    """Raw intraday bar-sell history: columns hour, open, high, low, close (for backfill)."""
    df = pd.DataFrame(_get(f"{BASE}/ohlc"))
    df["hour"] = pd.to_datetime(df["hour"])  # naive = asia/bangkok
    return df


def fetch_latest_thaigold() -> GoldTick:
    """Fallback live tick from the thaigold.info mirror (no Cloudflare)."""
    rows = _get(THAIGOLD)
    by = {r.get("name"): r for r in rows}

    def f(name: str, key: str) -> float | None:
        try:
            return float(str(by.get(name, {}).get(key, "")).replace(",", ""))
        except (TypeError, ValueError):
            return None

    now = datetime.now(BANGKOK).replace(tzinfo=None).isoformat(timespec="seconds")
    return GoldTick(
        as_time=now,
        bar_buy=f("96.5%", "bid"),     # bid = รับซื้อ (what we sell into)
        bar_sell=f("96.5%", "ask"),    # ask = ขายออก
        ornament_buy=f("สมาคมฯ", "bid"),
        gold_spot_usd=f("GoldSpot", "bid"),
        baht_per_usd=f("THB", "bid"),
    )


def get_live_tick() -> GoldTick:
    """GTA /Latest, falling back to thaigold.info if GTA blocks (403 from datacenter IPs)."""
    try:
        return fetch_latest()
    except Exception as exc:  # noqa: BLE001 - any failure -> try the mirror
        print(f"GTA /Latest failed ({type(exc).__name__}: {exc}); falling back to thaigold.info")
        return fetch_latest_thaigold()


def ohlc_to_daily(df: pd.DataFrame) -> pd.DataFrame:
    """Reduce intraday bar-sell rows to one OHLC row per Bangkok calendar day."""
    df = df.copy()
    df["trade_date"] = df["hour"].dt.date
    daily = df.groupby("trade_date").agg(
        bar_sell_open=("open", "first"),
        bar_sell_high=("high", "max"),
        bar_sell_low=("low", "min"),
        bar_sell_close=("close", "last"),
    )
    return daily.reset_index().sort_values("trade_date")


def _dry_run() -> None:
    tick = get_live_tick()
    print("LATEST:", tick.as_time, "round", tick.seq)
    print(f"  bar buy-in {tick.bar_buy:,.0f}  sell-out {tick.bar_sell:,.0f}  spot ${tick.gold_spot_usd}  USDTHB {tick.baht_per_usd}")


if __name__ == "__main__":
    _dry_run()
