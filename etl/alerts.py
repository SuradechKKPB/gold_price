"""LINE push alerts on sell-pressure VERDICT TRANSITIONS.

Two senders feed the family's LINE, by design:
  - the Cloudflare Worker (worker/) sends the fixed-time DAILY DIGEST at 06:00 / 15:00
    ICT — routine, fires whether or not anything moved. It lives there because CF cron
    is precise to the second while GitHub's scheduler can be 5-40 min late.
  - THIS module fires an EVENT alert only when the verdict CHANGES (hold -> trim ->
    tranche -> sell, or a downgrade), from the GitHub cron (etl.compute). It dedups via
    etl.state so each transition pings exactly once, not once per cron run.

Quota is the binding constraint: broadcast bills per follower, and one free OA allows 300
messages/month. send_line_broadcast therefore fails over to a second OA once the first is
spent. Do not add a third sender here without recounting the monthly budget.
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


def alert_on_transition(sb, scores, *, buy_in: float | None = None, extra: str = "") -> str:
    """Broadcast iff the latest verdict differs from the last one we alerted on.

    Missing state anchors to a NEUTRAL 'hold' baseline, NOT the live verdict: a standing
    elevated signal (trim/tranche/sell) then fires on the first run instead of being
    silently adopted — the bug that swallowed the 2026-07-08 sell. At 'hold' there is
    nothing to announce, so the baseline is just persisted once and no spurious deploy-time
    alert goes out. State advances only after a successful send, so a LINE outage retries.

    Returns a STATUS, not a bool: "sent", "none" (no transition to announce), "baseline"
    (first run — the neutral anchor was persisted) or "failed" (a real transition that LINE
    would not take). The caller must not collapse the last two into one cheerful "No alert."
    — a dropped sell signal and a quiet market would print the same line.
    """
    valid = scores.dropna(subset=["sell_pressure"])
    if not len(valid):
        return "none"
    row = valid.iloc[-1]
    cur = row["verdict"]
    cur_date = valid.index[-1].date().isoformat()

    stored_verdict, _ = state.get_alert_state(sb)
    last_verdict = stored_verdict if stored_verdict is not None else "hold"
    if cur == last_verdict:
        if stored_verdict is None:
            state.set_alert_state(sb, cur, cur_date, _LEVEL.get(cur, 0))  # persist baseline once
            return "baseline"
        return "none"

    if buy_in is None:
        buy_in = _latest_buy_in(sb)
    msg = _transition_message(last_verdict, cur, float(row["sell_pressure"]), buy_in, extra, as_of=cur_date)
    if send_line_broadcast(msg):
        state.set_alert_state(sb, cur, cur_date, _LEVEL.get(cur, 0))
        return "sent"
    # State deliberately NOT advanced: the next cron run retries this same transition.
    print(f"LINE: transition {last_verdict} -> {cur} was NOT delivered; will retry next run.")
    return "failed"


_STATUS_LINE = {
    "sent": "LINE transition alert sent.",
    "none": "No alert (no verdict change).",
    "baseline": "No alert (alert baseline initialised).",
    "failed": "ALERT UNDELIVERED — verdict changed but LINE refused; retrying next run.",
}


def status_line(status: str) -> str:
    """One-line cron summary for an alert_on_transition() status."""
    return _STATUS_LINE.get(status, f"Alert status: {status}.")
