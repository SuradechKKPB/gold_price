"""LINE push alerts on sell-pressure VERDICT TRANSITIONS.

Two channels feed Poom's LINE, by design:
  - the phone (joe-health phone_sync.py) sends a DAILY DIGEST every sync — "here is
    today's price + the current verdict". Routine, fires whether or not anything moved.
  - THIS module fires an EVENT alert only when the verdict CHANGES (hold→trim→
    tranche→sell, or a downgrade), from the GitHub cron (etl.compute) that actually
    runs on schedule. It dedups via etl.state so each transition pings exactly once,
    not every 6h.

The old maybe_alert (edge-triggered on a raw 50-crossing, called only from run.py
which returns early whenever GTA 403s the datacenter) could never fire on the cron —
so the automated system sent zero alerts. alert_on_transition replaces it.
"""

from __future__ import annotations

import httpx

from . import state
from .config import settings

LINE_BROADCAST = "https://api.line.me/v2/bot/message/broadcast"

_VERDICT_TH = {
    "hold": "ถือไว้",
    "trim": "ลดพอร์ตเล็กน้อย",
    "sell_tranche": "ขายบางส่วน",
    "sell": "ขายออก",
}
_LEVEL = {"hold": 0, "trim": 1, "sell_tranche": 2, "sell": 3}


def send_line_broadcast(text: str) -> bool:
    if not settings.line_channel_access_token:
        return False
    resp = httpx.post(
        LINE_BROADCAST,
        headers={"Authorization": f"Bearer {settings.line_channel_access_token}"},
        json={"messages": [{"type": "text", "text": text}]},
        timeout=20,
    )
    resp.raise_for_status()
    return True


def _latest_buy_in(sb) -> float | None:
    """Most recent association buy-in (what Poom sells into) for the alert body."""
    res = (
        sb.table("gold_price_daily")
        .select("bar_buy_close")
        .order("trade_date", desc=True)
        .limit(1)
        .execute()
    )
    if res.data and res.data[0].get("bar_buy_close") is not None:
        return float(res.data[0]["bar_buy_close"])
    return None


def _transition_message(prev: str, cur: str, score: float, buy_in: float | None, extra: str = "", as_of: str = "") -> str:
    up = _LEVEL.get(cur, 0) > _LEVEL.get(prev, 0)
    head = "⚠️ สัญญาณขายทองเข้มขึ้น" if up else "🟢 สัญญาณขายทองผ่อนลง"
    prev_th, cur_th = _VERDICT_TH.get(prev, prev), _VERDICT_TH.get(cur, cur)
    price = f"~{buy_in:,.0f} บาท/บาททอง" if buy_in else "—"
    body = (
        f"{head}\n"
        f"{prev_th} → {cur_th}\n"
        f"คะแนน {score:.0f}/100\n"
        f"ราคารับซื้อ {price}"
    )
    if as_of:
        body += f"\nข้อมูล ณ {as_of}"   # surface the data date so a stale (weekend/holiday) signal is visible, not hidden
    if extra:
        body += f"\n{extra}"
    return f"{body}\nดูรายละเอียด: {settings.dashboard_url}"


def alert_on_transition(sb, scores, *, buy_in: float | None = None, extra: str = "") -> bool:
    """Broadcast iff the latest verdict differs from the last one we alerted on.

    Missing state anchors to a NEUTRAL 'hold' baseline, NOT the live verdict: a standing
    elevated signal (trim/tranche/sell) then fires on the first run instead of being
    silently adopted — the bug that swallowed the 2026-07-08 sell. At 'hold' there is
    nothing to announce, so the baseline is just persisted once and no spurious deploy-time
    alert goes out. State advances only after a successful send, so a LINE outage retries.
    """
    valid = scores.dropna(subset=["sell_pressure"])
    if not len(valid):
        return False
    row = valid.iloc[-1]
    cur = row["verdict"]
    cur_date = valid.index[-1].date().isoformat()

    stored_verdict, _ = state.get_alert_state(sb)
    last_verdict = stored_verdict if stored_verdict is not None else "hold"
    if cur == last_verdict:
        if stored_verdict is None:
            state.set_alert_state(sb, cur, cur_date, _LEVEL.get(cur, 0))  # persist baseline once
        return False

    if buy_in is None:
        buy_in = _latest_buy_in(sb)
    msg = _transition_message(last_verdict, cur, float(row["sell_pressure"]), buy_in, extra, as_of=cur_date)
    if send_line_broadcast(msg):
        state.set_alert_state(sb, cur, cur_date, _LEVEL.get(cur, 0))
        return True
    return False
