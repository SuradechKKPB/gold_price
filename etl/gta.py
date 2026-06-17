"""Gold Traders Association of Thailand (สมาคมค้าทองคำ) data client.

Two endpoints (free, unauthenticated JSON; require a browser User-Agent):
  - /Latest : current round snapshot, incl. the seller-relevant bar buy-in (bL_BuyPrice).
  - /ohlc   : ~20yr intraday history of the 96.5% bar SELL series, since 2006-05-01.

We sell at the buy-in (bid). The /ohlc series is the sell-out (ask); the two move
together, so timing signals use the long /ohlc history and the ~200 THB spread is
modeled as a transaction cost. Live buy-in is captured from /Latest going forward.
"""

from __future__ import annotations

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from tenacity import retry, stop_after_attempt, wait_exponential
import httpx

BASE = "https://www.goldtraders.or.th/api/GoldPrices"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


class GoldTick(BaseModel):
    """One GTA round snapshot from /Latest."""

    model_config = ConfigDict(populate_by_name=True)

    gold_price_id: int = Field(alias="goldPriceID")
    as_time: str = Field(alias="asTime")  # bangkok wall-clock, e.g. 2026-06-17T12:58:00
    seq: int = Field(alias="seq")
    bar_buy: float | None = Field(default=None, alias="bL_BuyPrice")
    bar_sell: float | None = Field(default=None, alias="bL_SellPrice")
    ornament_buy: float | None = Field(default=None, alias="oM965_BuyPrice")
    gold9999_buy: float | None = Field(default=None, alias="oM9999_BuyPrice")
    gold_spot_usd: float | None = Field(default=None, alias="goldSpot")
    baht_per_usd: float | None = Field(default=None, alias="bahtPerUSD")
    chg_prev_row: float | None = Field(default=None, alias="priceChangeFromPrevRow")
    chg_prev_day: float | None = Field(default=None, alias="priceChangeFromPrevDayLast")


@retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=1, min=2, max=30))
def _get_json(path: str) -> object:
    with httpx.Client(timeout=30, headers=HEADERS, follow_redirects=True) as client:
        resp = client.get(f"{BASE}/{path}")
        resp.raise_for_status()
        return resp.json()


def fetch_latest() -> GoldTick:
    """Current round snapshot, including the live bar buy-in."""
    return GoldTick.model_validate(_get_json("Latest"))


def fetch_ohlc() -> pd.DataFrame:
    """Raw intraday bar-sell history: columns hour, open, high, low, close."""
    rows = _get_json("ohlc")
    df = pd.DataFrame(rows)
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
    """Verify the read path end-to-end without touching the database."""
    tick = fetch_latest()
    print("LATEST:", tick.as_time, "round", tick.seq)
    print(f"  bar buy-in (sell into): {tick.bar_buy:,.0f}   bar sell-out: {tick.bar_sell:,.0f}")
    print(f"  spot ${tick.gold_spot_usd:,.0f}/oz   USDTHB {tick.baht_per_usd}")

    daily = ohlc_to_daily(fetch_ohlc())
    print(f"\nOHLC daily rows: {len(daily):,}  ({daily['trade_date'].min()} -> {daily['trade_date'].max()})")
    print(daily.tail(3).to_string(index=False))


if __name__ == "__main__":
    _dry_run()
