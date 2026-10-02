import numpy as np

from etl import backtest, plan


def test_day_skill_is_zero_on_a_pure_trend():
    """A centred window cancels drift: on a straight line, no choice of days shows skill."""
    price = np.linspace(20000, 80000, 2000)
    nb = backtest.centred_mean(price)
    for days in (np.arange(100, 1800, 7), np.arange(1500, 1800), np.arange(100, 400)):
        assert abs(backtest.day_skill(price, days, nb)) < 1e-9


def test_day_skill_rewards_selling_the_local_top():
    t = np.arange(2000)
    price = 50000 + 2000 * np.sin(2 * np.pi * t / 126)
    nb = backtest.centred_mean(price)
    tops = np.array([i for i in range(200, 1800) if price[i] >= price[i - 1] and price[i] >= price[i + 1]])
    lows = np.array([i for i in range(200, 1800) if price[i] <= price[i - 1] and price[i] <= price[i + 1]])
    assert backtest.day_skill(price, tops, nb) > 0.03
    assert backtest.day_skill(price, lows, nb) < -0.03


def test_campaign_replay_matches_the_live_decide():
    price = np.linspace(100, 200, 400)
    L, rules = 63, plan.Rules()
    neutral = np.full(len(price), "neutral", dtype=object)
    avg, day, fills = backtest.run_campaigns(price, neutral, L, rules, [0, 50])
    expected = np.array(plan.slot_ends(L, rules.n_tranches)) + 1
    assert (fills[0] == expected).all() and (fills[1] == 50 + expected).all()
    assert day[0] == expected.mean()


def test_slot_mid_sits_inside_each_slot():
    L, n = 63, 5
    _, _, fills = backtest.slot_mid(np.arange(200.0), L, n, [0])
    ends = (-1,) + plan.slot_ends(L, n)
    for k, f in enumerate(fills[0]):
        assert ends[k] + 1 <= f - 1 <= ends[k + 1]
