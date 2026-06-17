"""Gold Traders Association of Thailand (สมาคมค้าทองคำ) data client.

GTA endpoints are free JSON but Cloudflare-fronted and BLOCK many datacenter IPs
(e.g. GitHub Actions) with 403, while residential/Thai IPs work. We send full
browser headers to maximise success. There is no reliable free fresh fallback
(thaigold.info serves stale cached values; global XAU/FX APIs also block from
datacenters), so the orchestrator SKIPS safely when GTA is unreachable rather
than writing bad data.

We sell at the buy-in (bid). /ohlc is the bar sell-out history (one-time backfill).
"""

from __future__ import annotations

import httpx
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from tenacity import retry, stop_after_attempt, wait_exponential

BASE = "https://www.goldtraders.or.th/api/GoldPrices"

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
    as_time: str = Field(alias="asTime")
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
    tick = fetch_latest()
    print("LATEST:", tick.as_time, "round", tick.seq, "bar buy-in", tick.bar_buy)


if __name__ == "__main__":
    _dry_run()
