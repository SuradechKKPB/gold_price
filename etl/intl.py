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

Sources, and the one seam between them:
  - history: LBMA gold fix (PM, AM fallback) x frankfurter.dev (ECB) for USD/THB. Keyless
    and reachable from any IP.
  - ongoing: fetch_live_intl() reads keyless spot feeds datacenters can reach (unlike
    GTA), and topup_from_daily() re-derives recent days from the spot/fx the Cloudflare
    Worker stores on each GTA sync. Either way the cron needs no phone.

The fix and spot measure DIFFERENT things: the fix is a 15:00 London snapshot, spot is the
session close, and they can sit 2-3% apart when gold moves after the fix (2026-08-28: 3.2%).
No free source covers 20 years of closes, so history is fix-based and everything from mid-
2026 on is close-based, with a single one-off boundary between them. backfill() will not
overwrite a day already recorded from spot, so each day keeps one basis permanently —
without that the two fought over the trailing window and a bar silently changed value once
it aged past it.
"""

from __future__ import annotations

import datetime as dt

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



# Sources that measure a daily CLOSE from spot. Once a day is recorded from one of these
# it keeps that basis for good — see backfill().
_SPOT_SOURCES = ("gta_spot", "live_api", "gta_tick", "phone_spot")


def backfill(sb, start: str = "2006-01-01") -> int:
    """Load LBMA-fix history into macro_daily(series='gold_intl_thb'). Run locally.

    Idempotent, and deliberately WON'T touch a day already recorded from spot. The two
    sources measure different things: the LBMA fix is a 15:00 London snapshot, spot is the
    session close, and on 2026-08-28 they sat 3.2% apart because gold sold off after the
    fix. Neither is wrong, but a day must keep ONE of them.

    Without this guard the bases fought over the trailing window that topup_from_daily
    re-derives: a bar was spot while it was recent, then flipped to fix once it aged out.
    A day's value changing weeks after the fact is unacceptable in a series whose whole
    job is to say how far price has fallen from its recent high — the 40-day rolling max
    behind dd_from_high would shift under the score. Freezing the basis per day means the
    only fix/spot boundary is the one-off point where spot coverage begins.
    """
    ser = build_intl_thb(start)
    rows: list[dict] = []
    page = 0
    while True:
        res = (
            sb.table("macro_daily").select("trade_date,source").eq("series", SERIES)
            .order("trade_date").range(page * 1000, page * 1000 + 999).execute()
        )
        rows.extend(res.data)
        if len(res.data) < 1000:
            break
        page += 1
    frozen = {r["trade_date"] for r in rows if r.get("source") in _SPOT_SOURCES}
    keep = ser[[d.date().isoformat() not in frozen for d in ser.index]]
    if len(ser) - len(keep):
        print(f"backfill: leaving {len(ser) - len(keep)} spot-based day(s) untouched.")
    return _upsert(sb, keep, "lbma_x_frankfurter")


def fetch_live_intl() -> tuple[float | None, float | None, float | None]:
    """Live world gold in THB from KEYLESS sources that answer from ANY IP — including
    GitHub's datacenter runners (unlike GTA, which 403s them). This is what lets the cron
    keep the SCORE fresh with no phone in the loop: the score basis is the world price, and
    the world price is public. Each leg has a fallback. Returns (xau_usd, usd_thb, intl_thb),
    any of which may be None if every source for that leg failed."""
    xau = None
    for url, pick in (
        ("https://api.gold-api.com/price/XAU", lambda j: j.get("price")),
        (LBMA_PM, lambda j: next((r["v"][0] for r in reversed(j) if r.get("v") and r["v"][0] is not None), None)),
    ):
        try:
            v = pick(httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20).json())
            if v:
                xau = float(v); break
        except Exception:  # noqa: BLE001
            continue
    fx = None
    for url, pick in (
        (f"{FRANKFURTER}/latest?base=USD&symbols=THB", lambda j: j["rates"]["THB"]),
        ("https://open.er-api.com/v6/latest/USD", lambda j: j["rates"]["THB"]),
    ):
        try:
            v = pick(httpx.get(url, timeout=20).json())
            if v:
                fx = float(v); break
        except Exception:  # noqa: BLE001
            continue
    return xau, fx, (xau * fx * CONV if xau and fx else None)


def bkk_today() -> pd.Timestamp:
    """Today's Asia/Bangkok calendar date, as a tz-naive midnight Timestamp.

    trade_date is defined as the BANGKOK calendar date (see 0001_init.sql). The cron runs
    on UTC runners, and two of its five slots (22:30 and 18:00 UTC) fire at 05:30 and 01:00
    ICT the NEXT Bangkok day — so a naive Timestamp.today() there stamps the row with
    YESTERDAY's Bangkok date and silently overwrites that day's close with a price from the
    following morning. That is a T+1 leak into a series the backtest assumes ends at T.
    """
    return pd.Timestamp(dt.datetime.now(dt.timezone(dt.timedelta(hours=7))).date())


# A live quote this far from the last stored bar is corroborated against an independent
# source before it is written. Daily moves have a ~1.1% standard deviation, so 4% is
# ~3.6 sigma: rare enough that checking costs nothing, loose enough that ordinary
# volatility never trips it.
SUSPECT_MOVE = 0.04
# Two SPOT sources are considered to agree within this band.
AGREE_BAND = 0.02


def _last_stored(sb) -> float | None:
    res = (
        sb.table("macro_daily")
        .select("value")
        .eq("series", SERIES)
        .order("trade_date", desc=True)
        .limit(1)
        .execute()
    )
    return float(res.data[0]["value"]) if res.data and res.data[0].get("value") is not None else None


def _gta_spot_thb(sb) -> float | None:
    """Most recent GTA-reported world spot, converted to the THB bar basis.

    The corroborating source must measure the SAME thing as the quote being checked. GTA
    publishes `goldSpot` alongside its Thai quote, and it is spot — independent of
    gold-api.com but on the same basis. The LBMA fix is NOT usable here: it is a 15:00
    London snapshot, so spot legitimately sits 2-3% away from it by the end of the same
    session, and checking a spot quote against a fix rejects perfectly good data.
    """
    res = (
        sb.table("gold_price_daily")
        .select("gold_spot_usd,baht_per_usd")
        .not_.is_("gold_spot_usd", "null")
        .not_.is_("baht_per_usd", "null")
        .order("trade_date", desc=True)
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    r = res.data[0]
    return float(r["gold_spot_usd"]) * float(r["baht_per_usd"]) * CONV


def _market_day(d: dt.date) -> dt.date:
    """Map a calendar date onto the trading day whose close it represents.

    Gold trades until ~22:00 UTC Friday and reopens Sunday evening, so a quote sampled on
    Saturday or Sunday IS Friday's close — the same number, read later. It belongs on
    Friday's bar, not on a weekend bar the world market never printed.
    """
    return d - dt.timedelta(days=d.weekday() - 4) if d.weekday() >= 5 else d


def topup_live(sb, trade_date=None) -> float | None:
    """Self-sufficient daily refresh: derive the current world bar from keyless feeds and
    upsert it. Returns the value written, or None if nothing was written.

    WEEKENDS are folded onto Friday rather than skipped. An earlier version of this guard
    skipped them, on the theory that a Saturday quote 2-3% below Friday's LBMA fix had to
    be a thin bad print. It was not: on 2026-08-29 gold-api.com read $4,456 and GTA's own
    goldSpot read $4,455 — 0.03% apart — while GTA's Thai quote fell 1,600 THB the same
    morning. Gold really did sell off after Friday's 15:00 London fix, and the guard was
    discarding a real move. What the number is NOT is a Saturday bar; it is Friday's
    close, so _market_day puts it there.

    CORROBORATION still applies to moves beyond SUSPECT_MOVE, but against GTA's spot
    rather than the LBMA fix — see _gta_spot_thb for why checking spot against a fix
    rejects good data. It stays a corroboration rule and not a magnitude cap: a genuine
    crash is the one event this tool exists to catch, and a cap would blind it exactly
    then. If the second source is unavailable the move is accepted, because refusing to
    record real selloffs is the worse failure for a sell-timing tool.
    """
    raw = bkk_today().date() if trade_date is None else pd.Timestamp(trade_date).date()
    d = _market_day(raw)

    xau, fx, val = fetch_live_intl()
    if val is None:
        return None

    last = _last_stored(sb)
    if last and abs(val / last - 1.0) > SUSPECT_MOVE:
        move = (val / last - 1.0) * 100
        ref = _gta_spot_thb(sb)
        if ref is not None and abs(val / ref - 1.0) > AGREE_BAND:
            print(
                f"topup_live: REJECTED {val:,.0f} for {d} ({move:+.1f}% vs last bar "
                f"{last:,.0f}); GTA spot says {ref:,.0f}. Sources disagree, nothing written."
            )
            return None
        seen = f"GTA spot {ref:,.0f}" if ref is not None else "no second source"
        print(f"topup_live: {move:+.1f}% move on {d} accepted ({seen}).")

    if raw != d:
        print(f"topup_live: {raw} is a {raw.strftime('%A')} — recording as {d}'s close.")
    _upsert(sb, pd.Series({pd.Timestamp(d): val}), "live_api")
    return val


def topup_from_daily(sb, days: int = 21) -> int:
    """Refresh the LAST `days` days from goldSpot x bahtPerUSD in gold_price_daily, which
    the Cloudflare Worker writes on each GTA sync.

    No external call, so the GitHub compute-only cron stays self-sufficient. These rows
    win over the backfill for the most-recent days (same basis, just fresher).

    BOUNDED on purpose: an earlier version upserted EVERY such day, so each run rewrote
    finalized LBMA-fix history rows with intraday snapshots — permanent basis drift across
    the whole series. We now only touch the recent window; the deep history stays on its
    authoritative LBMA x ECB backfill.

    NOTE (finalization): a value inside this window is the latest intraday snapshot, not
    the London PM fix. Re-running etl.intl.backfill overwrites it with the true fix once
    published — worth doing periodically, since nothing does it automatically."""
    cutoff = (bkk_today() - pd.Timedelta(days=days)).date().isoformat()
    rows = (
        sb.table("gold_price_daily")
        .select("trade_date,gold_spot_usd,baht_per_usd")
        .gte("trade_date", cutoff)
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
    # Thai dealers quote on some Saturdays; the world market does not trade then, so those
    # rows carry Friday's close read later. Fold them onto Friday and keep the LAST reading
    # for each trading day rather than dropping real information on the floor.
    ser.index = pd.DatetimeIndex([pd.Timestamp(_market_day(d.date())) for d in ser.index])
    ser = ser.groupby(level=0).last()
    return _upsert(sb, ser, "gta_spot") if len(ser) else 0


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
