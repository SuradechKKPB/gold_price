"""LINE alerts: the market entering its sell zone, and (optionally) the sell plan's steps.

Two senders feed the family's LINE, by design:
  - the Cloudflare Worker (worker/) sends the fixed-time DAILY DIGEST at 06:00 / 15:00
    ICT — routine, fires whether or not anything moved. It lives there because CF cron
    is precise to the second while GitHub's scheduler can be 5-40 min late.
  - THIS module fires an EVENT alert from the GitHub cron (etl.compute), deduped via
    etl.state so each event pings exactly once, not once per cron run.

Events (v4, see etl/signals.py and etl/plan.py):
  - MARKET: the verdict ENTERS very_rich, the zone the plan sells into early. That is the
    one market moment worth interrupting everyone for, and it happens ~2-3 times a year.
    Leaving the zone, and the brake, are carried by the digest, not pinged.
  - PLAN: a tranche becomes sellable, waits out a brake, or the plan completes. Sent only
    when settings.plan_in_broadcast is on, because a broadcast reaches every follower and
    the plan is personal. When both fire in one run they share one message.

The pre-v4 alert keyed on hold/trim/sell_tranche/sell transitions (state key `last_alert`).
That key is no longer read, so the switch to v4 cannot replay an old transition.

Quota is the binding constraint: broadcast bills per follower, and one free OA allows 300
messages/month. send_line_broadcast therefore fails over to a second OA once the first is
spent. Do not add a third sender here without recounting the monthly budget.
"""

from __future__ import annotations

import json

import httpx
import numpy as np

from . import state
from .config import settings

LINE_BROADCAST = "https://api.line.me/v2/bot/message/broadcast"
LINE_INFO = "https://api.line.me/v2/bot/info"
LINE_QUOTA = "https://api.line.me/v2/bot/message/quota"
LINE_CONSUMPTION = "https://api.line.me/v2/bot/message/quota/consumption"

VERDICT_TH = {
    "weak": "ราคาอ่อนตัว (ยังไม่ควรขาย)",
    "neutral": "ปกติ",
    "rich": "โซนแพง",
    "very_rich": "โซนแพงมาก (จังหวะขาย)",
}
_PLAN_EVENTS = ("sell", "wait", "done")
MARKET_COOLDOWN = 5   # trading days (by bar date) between two sell-zone pings


def send_line_broadcast(text: str) -> bool:
    """Broadcast with OA failover: primary OA first; if it fails (esp. 429 = the free
    300-msg/month quota is exhausted), retry from the secondary OA. Two free OAs ≈ 600/mo.

    Every outcome is printed. A swallowed failure here is indistinguishable from "nothing
    to say" in the cron log, and that is exactly how a dead alert path stays dead: on
    2026-08-24 the primary OA's quota ran out, the failover did not exist yet, and the only
    evidence anyone had was a family member noticing the silence.
    """
    tokens = [
        (name, tok)
        for name, tok in (("primary", settings.line_channel_access_token),
                          ("fallback", settings.line_channel_access_token_2))
        if tok
    ]
    if not tokens:
        print("LINE: no channel token configured — cannot broadcast.")
        return False
    for name, tok in tokens:
        try:
            resp = httpx.post(
                LINE_BROADCAST,
                headers={"Authorization": f"Bearer {tok}"},
                json={"messages": [{"type": "text", "text": text}]},
                timeout=20,
            )
            if resp.status_code < 300:
                print(f"LINE: broadcast sent from the {name} OA.")
                return True
            spent = " — monthly quota spent" if resp.status_code == 429 else ""
            print(f"LINE: {name} OA refused ({resp.status_code}){spent}.")
        except Exception as exc:  # noqa: BLE001
            print(f"LINE: {name} OA raised {exc!r}.")
    print("LINE: every OA refused — nothing was delivered.")
    return False


def check_channels() -> bool:
    """Verify every configured OA answers, and print its remaining monthly allowance.

    The event alert is the message that actually matters, and it fires a handful of times
    a YEAR (the market enters very_rich ~2-3 times). Under v3, between 2026-08-05 and
    2026-09-14 not one went out because the verdict never changed. So a revoked or rotated
    token on this side can sit dead for months and only announce itself by swallowing the
    sell signal it existed to deliver. These three endpoints are reads: they cost no quota,
    so the cron can afford them on every run.

    Returns False only when NO configured OA answers — one dead OA still has a failover.
    """
    configured = [
        (name, tok)
        for name, tok in (("primary", settings.line_channel_access_token),
                          ("fallback", settings.line_channel_access_token_2))
        if tok
    ]
    if not configured:
        print("LINE: no channel token configured — alerts cannot be delivered.")
        return False

    alive = 0
    for name, tok in configured:
        headers = {"Authorization": f"Bearer {tok}"}
        try:
            info = httpx.get(LINE_INFO, headers=headers, timeout=15)
            if info.status_code >= 300:
                print(f"LINE: {name} OA token REJECTED ({info.status_code}) — rotate it.")
                continue
            alive += 1
            oa = info.json().get("basicId", "?")
            quota = httpx.get(LINE_QUOTA, headers=headers, timeout=15).json()
            used = httpx.get(LINE_CONSUMPTION, headers=headers, timeout=15).json().get("totalUsage", 0)
            cap = quota.get("value")
            left = "unlimited" if cap is None else f"{cap - used} of {cap} left"
            print(f"LINE: {name} OA {oa} ok — {left} this month.")
        except Exception as exc:  # noqa: BLE001
            # A network blip here says nothing about the token; do not cry wolf.
            print(f"LINE: {name} OA health check inconclusive ({exc!r}).")
            alive += 1
    if not alive:
        print("LINE: every configured OA rejected its token — NO alert can be delivered.")
    return alive > 0


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


def _market_event(sb, scores) -> tuple[bool, str, str]:
    """(announce?, current verdict, its bar date).

    Keyed to the BAR, not to the run. Today's bar is re-scored on each of the five daily
    runs with a fresh live price. Hysteresis acts between bars, not within one, so today's
    verdict can flip at 90 from one run to the next, and a per-run key would re-ping every
    follower on each flip. An entry is a bar whose verdict is very_rich while the previous
    bar's was not. It is announced at most once per MARKET_COOLDOWN trading days, which also
    absorbs a bar that enters, drops back and re-enters the next day. With no state at all
    (the first v4 run), a standing very_rich zone is announced rather than silently adopted.
    """
    valid = scores.dropna(subset=["sell_pressure"])
    cur = str(valid["verdict"].iloc[-1])
    prev = str(valid["verdict"].iloc[-2]) if len(valid) > 1 else "neutral"
    cur_date = valid.index[-1].date().isoformat()
    if cur != "very_rich":
        return False, cur, cur_date
    last = (state.get_state(sb, "market_alert") or {}).get("text")   # bar date of the last ping
    if last is None:
        return True, cur, cur_date
    if prev == "very_rich":
        return False, cur, cur_date
    return int(np.busday_count(last, cur_date)) >= MARKET_COOLDOWN, cur, cur_date


def _plan_key(status: dict | None) -> str | None:
    """Dedup key of a plan event: one ping per tranche per KIND of event. An early rich-day
    sale that was not taken must not silence the reminder when the tranche falls due, so
    'rich' and 'due' are separate kinds. 'deadline' folds into 'due': it is the same tranche
    still unsold."""
    if not status or status.get("action") not in _PLAN_EVENTS:
        return None
    kind = status["action"]
    if kind == "sell":
        kind = "sell_rich" if status.get("reason") == "rich" else "sell_due"
    return f"{kind}|{status.get('tranche')}|{status.get('start')}"


def _plan_lines(s: dict) -> list[str]:
    n, k = s["n_tranches"], s.get("tranche")
    if s["action"] == "done":
        return [f"✅ แผนขายทอง: ขายครบ {n}/{n} ไม้ตามแผนแล้ว"]
    if s["action"] == "wait":
        return [f"⏸ แผนขายทอง: ไม้ {k}/{n} ครบกำหนดแต่ราคาอ่อนตัว — รอได้ถึง {s['wait_until']}"]
    why = {"rich": "ราคาโซนแพงมาก", "due": "ครบกำหนดไม้นี้", "deadline": "ใกล้เส้นตายของแผน"}.get(s["reason"], s["reason"])
    count = int(s.get("count") or 1)
    what = f"ไม้ {k}/{n} ขายได้รอบนี้" if count == 1 else f"ขาย {count} ไม้รอบนี้ (ไม้ {k}–{k + count - 1}/{n})"
    return [f"🟡 แผนขายทอง: {what} ({why})"]


def build_message(market: bool, verdict: str, score: float, as_of: str, plan_status: dict | None,
                  buy_in: float | None, extra: str = "") -> str:
    lines: list[str] = []
    if plan_status is not None:
        lines += _plan_lines(plan_status)
    if market:
        lines.append("📈 ทองเข้าโซนแพงมาก")
    lines.append(f"คะแนนจังหวะขาย {score:.0f}/100 — {VERDICT_TH.get(verdict, verdict)}")
    lines.append(f"ราคารับซื้อ {'~' + format(buy_in, ',.0f') + ' บาท/บาททอง' if buy_in else '—'}")
    lines.append(f"ข้อมูล ณ {as_of}")   # a stale (weekend/holiday) signal must show its date
    if extra:
        lines.append(extra)
    lines.append(f"ดูรายละเอียด: {settings.dashboard_url}")
    return "\n".join(lines)


def alert(sb, scores, plan_status: dict | None, *, buy_in: float | None = None, extra: str = "") -> str:
    """Send at most one message for this run's events. Returns a STATUS:
    "sent", "none" (nothing to announce) or "failed" (an event LINE would not take).

    State advances only after a successful send, so a LINE outage retries the same event
    on the next run. `plan_alert` holds every plan key already sent in this campaign, not
    just the last one. A tranche can flip between wait and sell_due as the brake opens and
    closes, and a single stored key would re-ping on each flip.
    """
    if not len(scores.dropna(subset=["sell_pressure"])):
        return "none"
    market, cur, cur_date = _market_event(sb, scores)
    key = _plan_key(plan_status) if settings.plan_in_broadcast else None
    raw = (state.get_state(sb, "plan_alert") or {}).get("text")
    sent_keys: list[str] = json.loads(raw) if raw and raw.startswith("[") else []
    plan_due = key is not None and key not in sent_keys

    if not market and not plan_due:
        return "none"

    if buy_in is None:
        buy_in = _latest_buy_in(sb)
    score = float(scores.dropna(subset=["sell_pressure"])["sell_pressure"].iloc[-1])
    msg = build_message(market, cur, score, cur_date, plan_status if plan_due else None, buy_in, extra)
    if not send_line_broadcast(msg):
        print("LINE: event was NOT delivered; will retry next run.")
        return "failed"
    if market:
        state.set_state(sb, "market_alert", text=cur_date)
    if plan_due:
        campaign = f"|{plan_status.get('start')}"
        keep = [k for k in sent_keys if k.endswith(campaign)] + [key]
        state.set_state(sb, "plan_alert", text=json.dumps(keep))
    return "sent"


_STATUS_LINE = {
    "sent": "LINE event alert sent.",
    "none": "No alert (no new event).",
    "failed": "ALERT UNDELIVERED — an event fired but LINE refused; retrying next run.",
}


def status_line(status: str) -> str:
    """One-line cron summary for an alert() status."""
    return _STATUS_LINE.get(status, f"Alert status: {status}.")
