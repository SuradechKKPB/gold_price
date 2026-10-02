import numpy as np
import pandas as pd
import pytest

from etl import indicators, signals


def _daily(n=1600, seed=1, shock_at=None, shock=1.15):
    rng = np.random.default_rng(seed)
    px = 30000 * np.exp(np.cumsum(rng.normal(0.0003, 0.011, n)))
    if shock_at is not None:
        px[shock_at:] *= shock
    dates = pd.bdate_range("2015-01-01", periods=n)
    return pd.DataFrame({"trade_date": dates.date, "bar_sell_close": px})


def _scores(**kw):
    return signals.compute_scores(indicators.build(_daily(**kw), 0.0))


@pytest.mark.parametrize("k", [900, 1200, 1500])
def test_no_score_moves_before_a_shock(k):
    """Causality: a shock on day k changes nothing dated before k."""
    base, hit = _scores(), _scores(shock_at=k)
    cut = pd.bdate_range("2015-01-01", periods=1600)[k]
    a, b = base[base.index < cut], hit[hit.index < cut]
    assert len(a) > 0
    for col in ("sell_pressure", "trend_break", "overbought", "momentum", "brake_dd"):
        pd.testing.assert_series_equal(a[col], b[col])
    assert (a["verdict"] == b["verdict"]).all()


def test_ranges_and_vocabulary():
    s = _scores()
    assert s["sell_pressure"].between(0, 100).all()
    assert set(s["verdict"]) <= set(signals.VERDICTS)
    assert s["brake_dd"].between(signals.BRAKE_FLOOR, signals.BRAKE_CAP).all()


def test_hysteresis_holds_a_tier_until_it_falls_a_margin_below():
    rich = np.array([81, 79, 77, 76, 74, 91, 86, 84])
    out = signals._verdict(rich, np.zeros(len(rich), bool), 80, 90, 5)
    assert list(out) == ["rich", "rich", "rich", "rich", "neutral", "very_rich", "very_rich", "rich"]


def test_brake_overrides_and_resets_the_tier():
    rich = np.array([95, 95, 95])
    out = signals._verdict(rich, np.array([False, True, False]), 80, 90, 5)
    assert list(out) == ["very_rich", "weak", "very_rich"]


def test_brake_needs_a_weak_rank_not_just_a_drawdown():
    s = _scores()
    assert not (s["brake"] & (s["sell_pressure"] >= signals.BRAKE_RICH_MAX)).any()
    assert ((s["verdict"] == "weak") == s["brake"]).all()
