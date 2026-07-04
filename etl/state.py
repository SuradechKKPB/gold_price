"""Tiny key/value state store for the ETL, layered over macro_daily.

We need a little durable state — the last verdict we alerted on (so the cron
alerts once per transition, not every 6h) and the score-formula version last
written to signals_daily (so a formula change auto-heals the whole history).
The gold Supabase is reachable only through PostgREST (service-role key), and
this environment has no DB password / CLI to run DDL, so a dedicated table is
not creatable from here. macro_daily already exists, is writable, and its
(trade_date, series) PK lets us store one row per state key under a sentinel
date + reserved series prefix that no reader of real series ever touches.

Swapping to a real `app_state` table later is a one-function change (get_state /
set_state); nothing else in the codebase knows where state lives.
"""

from __future__ import annotations

from supabase import Client

# A date that is never a real trade_date (history starts 2006) → invisible to any
# query that filters macro_daily by a real series like 'dxy' / 'gold_intl_thb'.
_SENTINEL_DATE = "2000-01-01"
_PREFIX = "app_state:"


def get_state(sb: Client, key: str) -> dict | None:
    """Return {'value': float|None, 'text': str|None} for a key, or None if unset."""
    res = (
        sb.table("macro_daily")
        .select("value,source")
        .eq("trade_date", _SENTINEL_DATE)
        .eq("series", _PREFIX + key)
        .limit(1)
        .execute()
    )
    if not res.data:
        return None
    row = res.data[0]
    return {"value": row.get("value"), "text": row.get("source")}


def set_state(sb: Client, key: str, value: float | None = None, text: str | None = None) -> None:
    sb.table("macro_daily").upsert(
        {
            "trade_date": _SENTINEL_DATE,
            "series": _PREFIX + key,
            "value": value,
            "source": text,
        },
        on_conflict="trade_date,series",
    ).execute()


# --- typed helpers -----------------------------------------------------------

def get_alert_state(sb: Client) -> tuple[str | None, str | None]:
    """(last_alerted_verdict, last_alert_date_iso) or (None, None)."""
    s = get_state(sb, "last_alert")
    if not s or not s.get("text"):
        return None, None
    verdict, _, date_iso = str(s["text"]).partition("|")
    return verdict or None, date_iso or None


def set_alert_state(sb: Client, verdict: str, date_iso: str, level: float) -> None:
    set_state(sb, "last_alert", value=level, text=f"{verdict}|{date_iso}")


def get_score_version(sb: Client) -> int | None:
    s = get_state(sb, "score_version")
    if not s or s.get("value") is None:
        return None
    return int(s["value"])


def set_score_version(sb: Client, version: int) -> None:
    set_state(sb, "score_version", value=float(version), text="signals_daily formula epoch")
