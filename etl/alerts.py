"""LINE push alerts when the sell-pressure score crosses the threshold.

Uses the LINE Messaging API broadcast endpoint (sends to everyone who added the
Official Account). No-ops gracefully when LINE_CHANNEL_ACCESS_TOKEN is unset.
"""

from __future__ import annotations

import httpx

from .config import settings
from .gta import GoldTick

LINE_BROADCAST = "https://api.line.me/v2/bot/message/broadcast"

_VERDICT_TH = {
    "hold": "ถือไว้",
    "trim": "ลดพอร์ตเล็กน้อย",
    "sell_tranche": "ขายบางส่วน",
    "sell": "ขายออก",
}


def _build_message(row, tick: GoldTick) -> str:
    verdict = _VERDICT_TH.get(row["verdict"], row["verdict"])
    price = f"{tick.bar_buy:,.0f}" if tick and tick.bar_buy else "-"
    return (
        "🔔 สัญญาณขายทองคำ\n"
        f"คะแนน {row['sell_pressure']:.0f}/100 — {verdict}\n"
        f"ราคารับซื้อ ~{price} บาท/บาททอง\n"
        f"ดูรายละเอียด: {settings.dashboard_url}"
    )


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


def maybe_alert(scores, tick: GoldTick) -> bool:
    """Broadcast when sell_pressure crosses UP through the threshold (today >= T > yesterday)."""
    valid = scores.dropna(subset=["sell_pressure"])
    if len(valid) < 2:
        return False
    today = valid.iloc[-1]
    prev = valid.iloc[-2]
    t = settings.alert_threshold
    if today["sell_pressure"] >= t > prev["sell_pressure"]:
        return send_line_broadcast(_build_message(today, tick))
    return False
