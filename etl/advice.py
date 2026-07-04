"""Personal decision overlay — turns the market SCORE into Poom-specific ADVICE.

signals_daily is a pure market property (the verdict of the world-gold trend). This
module layers on the things that are about *Poom's* exit, which must NOT contaminate
that history:

  - DEADLINE DECAY: an exit window has a hard end. Optimal stopping says the bar to
    sell should fall as the window ages (the option value of waiting shrinks). We decay
    the verdict cut-offs by elapsed fraction, so late in the window a middling score
    already reads as 'act'. Off unless SELL_WINDOW_START is set.
  - LOCAL PREMIUM: Poom realizes the association bid, not world parity. The bid-vs-parity
    spread is a real, mean-reverting edge — selling into a rich local premium adds THB the
    score (built on world price) is blind to. Surfaced as a z-score execution note.
  - ECONOMICS: proceeds for the whole holding vs a target and cost basis — so the tool can
    say "this already clears your goal by X%", not just "pressure is high".

Everything here is derived live from Supabase + config; nothing is written back into the
score history.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from . import signals
from .config import settings
from .load import fetch_daily, fetch_macro

DECAY_MAX = 14.0          # composite points the cut-offs drop by, linearly, over the window
PREM_WINDOW = 250         # trading days for the premium z-score baseline
_TIER_NAME = signals._TIER_NAME


def premium_series(sb) -> pd.Series:
    """Local premium = association ask / world parity − 1, per baht-weight, on intl dates."""
    intl = fetch_macro(sb, "gold_intl_thb")
    daily = fetch_daily(sb)
    ask = pd.Series(
        {pd.Timestamp(d): float(v) for d, v in zip(daily["trade_date"], daily["bar_sell_close"])}
    ).sort_index()
    ask = ask.reindex(intl.index, method="ffill")
    return (ask / intl - 1.0).dropna()


def premium_z(sb, window: int = PREM_WINDOW) -> pd.Series:
    p = premium_series(sb)
    mu = p.rolling(window, min_periods=60).mean()
    sd = p.rolling(window, min_periods=60).std(ddof=0)
    return ((p - mu) / sd.replace(0, pd.NA)).dropna()


def topup_premium(sb, days: int = 90) -> int:
    """Refresh the recent 'gold_premium_z' macro series for the dashboard."""
    z = premium_z(sb)
    if not len(z):
        return 0
    cutoff = pd.Timestamp.today().normalize() - pd.Timedelta(days=days)
    z = z[z.index >= cutoff]
    rows = [
        {"trade_date": d.date().isoformat(), "series": "gold_premium_z", "value": round(float(v), 3), "source": "advice"}
        for d, v in z.items()
    ]
    for i in range(0, len(rows), 1000):
        sb.table("macro_daily").upsert(rows[i : i + 1000], on_conflict="trade_date,series").execute()
    return len(rows)


def _elapsed_fraction(today: dt.date) -> float | None:
    if not settings.sell_window_start:
        return None
    try:
        start = dt.date.fromisoformat(settings.sell_window_start)
    except ValueError:
        return None
    total = max(1.0, settings.sell_window_months * 30.44)
    return max(0.0, min(1.0, (today - start).days / total))


def _tier(composite: float, n_trend: int, thr) -> int:
    t = 0
    if composite >= thr[0]:
        t = 1
    if composite >= thr[1]:
        t = 2
    if composite >= thr[2] and n_trend >= 2:
        t = 3
    return t


def build_advice(sb) -> dict:
    """Combine the latest market verdict with Poom's campaign context into one dict."""
    sig = (
        sb.table("signals_daily")
        .select("trade_date,sell_pressure,verdict,active_signals")
        .order("trade_date", desc=True)
        .limit(1)
        .execute()
        .data
    )
    if not sig:
        return {"ok": False, "reason": "no signals"}
    row = sig[0]
    score = float(row["sell_pressure"])
    verdict = row["verdict"]
    active = row.get("active_signals") or []
    n_trend = int("trailing_stop_fired" in active) + int("secular_confirm" in active)
    today = dt.date.fromisoformat(row["trade_date"])

    out: dict = {"ok": True, "date": row["trade_date"], "score": score, "verdict": verdict, "n_trend": n_trend}

    # --- deadline decay ---
    f = _elapsed_fraction(today)
    if f is not None:
        base = [signals.T_TRIM, signals.T_TRANCHE, signals.T_SELL]
        eff = [b - DECAY_MAX * f for b in base]
        eff_tier = _tier(score, n_trend, eff)
        raw_tier = _tier(score, n_trend, base)
        out["deadline"] = {
            "elapsed_frac": round(f, 3),
            "months_left": round(settings.sell_window_months * (1 - f), 1),
            "eff_thresholds": [round(x, 1) for x in eff],
            "eff_tier": _TIER_NAME[eff_tier],
            "urgent": eff_tier > raw_tier,
        }

    # --- local premium execution note ---
    z = premium_z(sb)
    if len(z):
        zc = float(z.iloc[-1])
        out["premium"] = {
            "z": round(zc, 2),
            "state": "rich" if zc >= 1 else "thin" if zc <= -1 else "normal",
        }

    # --- economics vs target / cost ---
    px = (
        sb.table("gold_price_daily").select("bar_buy_close").order("trade_date", desc=True).limit(1).execute().data
    )
    if px and px[0].get("bar_buy_close"):
        buy_in = float(px[0]["bar_buy_close"])
        proceeds = buy_in * settings.baht_weight
        econ = {"buy_in": buy_in, "proceeds_thb": round(proceeds)}
        if settings.target_thb > 0:
            econ["vs_target_pct"] = round((proceeds / settings.target_thb - 1) * 100, 1)
        if settings.cost_basis_thb_per_baht > 0:
            econ["pnl_pct"] = round((buy_in / settings.cost_basis_thb_per_baht - 1) * 100, 1)
        out["economics"] = econ

    return out


def advice_line(a: dict) -> str:
    """One-or-two extra LINE lines from build_advice (empty if nothing to add)."""
    if not a.get("ok"):
        return ""
    parts: list[str] = []
    d = a.get("deadline")
    if d and d.get("urgent"):
        parts.append(f"⏳ เหลือ ~{d['months_left']:.0f} เดือน — เกณฑ์ขายลดลง, สัญญาณเทียบเท่า “{_TH.get(d['eff_tier'], d['eff_tier'])}”")
    p = a.get("premium")
    if p and p["state"] == "rich":
        parts.append(f"💰 พรีเมียมในประเทศสูง (z={p['z']}) — จังหวะขายได้ราคาดีกว่าปกติ")
    elif p and p["state"] == "thin":
        parts.append(f"⚠️ พรีเมียมในประเทศต่ำ (z={p['z']}) — อาจรอให้ส่วนต่างปกติก่อนขาย")
    e = a.get("economics")
    if e and "vs_target_pct" in e:
        v = e["vs_target_pct"]
        parts.append(f"🎯 มูลค่ารวม {e['proceeds_thb']:,} บาท ({'เกินเป้า' if v>=0 else 'ต่ำกว่าเป้า'} {abs(v):.0f}%)")
    return "\n".join(parts)


_TH = {"hold": "ถือไว้", "trim": "ลดพอร์ตเล็กน้อย", "sell_tranche": "ขายบางส่วน", "sell": "ขายออก"}


def main() -> None:
    from . import load

    sb = load.client()
    import json

    print(json.dumps(build_advice(sb), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
