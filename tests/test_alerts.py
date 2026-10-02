import pandas as pd
import pytest

from etl import alerts, state
from etl.config import settings


@pytest.fixture
def env(monkeypatch):
    """In-memory state store and a recording LINE sender."""
    store: dict[str, dict] = {}
    sent: list[str] = []
    ok = {"value": True}
    monkeypatch.setattr(state, "get_state", lambda sb, k: store.get(k))
    monkeypatch.setattr(state, "set_state", lambda sb, k, value=None, text=None: store.__setitem__(k, {"value": value, "text": text}))
    monkeypatch.setattr(alerts, "send_line_broadcast", lambda text: (sent.append(text), ok["value"])[1])
    monkeypatch.setattr(alerts, "_latest_buy_in", lambda sb: 71650.0)
    monkeypatch.setattr(settings, "plan_in_broadcast", False)
    return store, sent, ok


def _bars(prev: str, cur: str, day: str = "2026-08-24", score: float = 91.0):
    """Two scored bars: the previous session's verdict and today's (possibly re-scored) one."""
    d = pd.Timestamp(day)
    return pd.DataFrame(
        {"sell_pressure": [80.0, score], "verdict": [prev, cur]},
        index=[d - pd.offsets.BDay(1), d],
    )


def test_quiet_market_sends_nothing(env):
    _, sent, _ = env
    assert alerts.alert(None, _bars("neutral", "rich", score=85), None) == "none"
    assert sent == []


def test_entry_fires_once_per_bar_even_when_reruns_flip_at_90(env):
    """Today's bar is re-scored five times a day; 91 → 89 → 90.4 must ping once, not twice."""
    _, sent, _ = env
    assert alerts.alert(None, _bars("rich", "very_rich", score=91), None) == "sent"
    assert alerts.alert(None, _bars("rich", "rich", score=89), None) == "none"
    assert alerts.alert(None, _bars("rich", "very_rich", score=90.4), None) == "none"
    assert len(sent) == 1 and "โซนแพงมาก" in sent[0]


def test_staying_in_the_zone_is_not_a_new_entry(env):
    _, sent, _ = env
    alerts.alert(None, _bars("rich", "very_rich", "2026-08-24"), None)
    assert alerts.alert(None, _bars("very_rich", "very_rich", "2026-08-25"), None) == "none"
    assert len(sent) == 1


def test_reentry_waits_out_the_cooldown(env):
    _, sent, _ = env
    alerts.alert(None, _bars("rich", "very_rich", "2026-08-24"), None)
    assert alerts.alert(None, _bars("rich", "very_rich", "2026-08-27"), None) == "none"   # 3 sessions later
    assert alerts.alert(None, _bars("rich", "very_rich", "2026-09-02"), None) == "sent"   # 7 sessions later
    assert len(sent) == 2


def test_standing_zone_is_announced_on_the_first_run(env):
    _, sent, _ = env
    assert alerts.alert(None, _bars("very_rich", "very_rich"), None) == "sent"


def test_failed_send_retries_next_run(env):
    store, sent, ok = env
    ok["value"] = False
    assert alerts.alert(None, _bars("rich", "very_rich"), None) == "failed"
    assert "market_alert" not in store
    ok["value"] = True
    assert alerts.alert(None, _bars("rich", "very_rich"), None) == "sent"


_PLAN = {"action": "sell", "reason": "due", "tranche": 2, "n_tranches": 5, "start": "2026-10-02", "wait_until": "2026-11-20"}


def test_plan_stays_off_the_broadcast_by_default(env):
    _, sent, _ = env
    assert alerts.alert(None, _bars("neutral", "neutral", score=60), _PLAN) == "none"
    assert sent == []


def test_plan_events_dedupe_per_tranche_when_public(env, monkeypatch):
    _, sent, _ = env
    monkeypatch.setattr(settings, "plan_in_broadcast", True)
    quiet = _bars("neutral", "neutral", score=60)
    assert alerts.alert(None, quiet, _PLAN) == "sent"
    assert alerts.alert(None, quiet, {**_PLAN, "reason": "deadline"}) == "none"
    assert alerts.alert(None, quiet, {**_PLAN, "tranche": 3}) == "sent"
    assert "ไม้ 2/5" in sent[0] and "ไม้ 3/5" in sent[1]


def test_brake_flapping_pings_each_kind_once(env, monkeypatch):
    _, sent, _ = env
    monkeypatch.setattr(settings, "plan_in_broadcast", True)
    quiet = _bars("neutral", "neutral", score=60)
    for action in ("wait", "sell", "wait", "sell", "wait"):
        alerts.alert(None, quiet, {**_PLAN, "action": action, "reason": "brake" if action == "wait" else "due"})
    assert len(sent) == 2


def test_market_and_plan_share_one_message(env, monkeypatch):
    _, sent, _ = env
    monkeypatch.setattr(settings, "plan_in_broadcast", True)
    alerts.alert(None, _bars("rich", "very_rich"), {**_PLAN, "reason": "rich"})
    assert len(sent) == 1 and "ไม้ 2/5" in sent[0] and "โซนแพงมาก" in sent[0]


def test_an_untaken_rich_sale_does_not_silence_the_due_reminder(env, monkeypatch):
    _, sent, _ = env
    monkeypatch.setattr(settings, "plan_in_broadcast", True)
    alerts.alert(None, _bars("rich", "very_rich"), {**_PLAN, "reason": "rich"})
    quiet = _bars("neutral", "neutral", "2026-10-21", score=60)
    alerts.alert(None, quiet, {**_PLAN, "action": "hold", "reason": "ahead"})
    alerts.alert(None, quiet, {**_PLAN, "reason": "due"})
    assert len(sent) == 2 and "ครบกำหนด" in sent[1]
