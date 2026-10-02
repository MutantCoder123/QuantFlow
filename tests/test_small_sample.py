"""Early estimates (2026-10-01): usable from the first trades because each
carries its range, instead of hiding every rate until 20 trades."""
import pytest

from performance.small_sample import early, expectancy_r, p_edge, profit_factor, win_rate


def test_win_rate_is_bayesian_with_a_range_that_narrows():
    few, many = win_rate([1, -1, 1, -1]), win_rate([1, -1] * 20)
    assert few["value"] == pytest.approx(50.0) and many["value"] == pytest.approx(50.0)
    assert (few["high"] - few["low"]) > 2 * (many["high"] - many["low"])
    assert win_rate([5])["value"] == pytest.approx(200 / 3)                # one win: 2/3, not 100%
    assert win_rate([])["value"] == pytest.approx(50.0)


def test_average_trade_and_the_chance_of_an_edge():
    rs = [1, -1, 2, -1, -1, 3, -1, 4, -1]
    e = expectancy_r(rs)
    assert e["value"] == pytest.approx(5 / 9) and e["low"] < 0 < e["high"]
    assert 70 < p_edge(rs)["value"] < 85
    assert p_edge([-1, -1, -1.2, -0.8])["value"] < 5
    assert p_edge([1])["value"] is None and expectancy_r([1])["low"] is None


def test_profit_factor_range_is_reproducible():
    nets = [1000, -1000, 2000, -1000, -1000, 3000, -1000, 4000, -1000]
    a, b = profit_factor(nets), profit_factor(nets)
    assert a == b and a["value"] == pytest.approx(2.0) and a["low"] < 2.0 < a["high"]
    assert profit_factor([500, 700])["low"] is None                         # no losing trade: no range


def test_early_bundle_reads_trades():
    out = early([{"net": 100.0, "r_net": 0.5}, {"net": -50.0, "r_net": -0.25}])
    assert set(out) == {"level", "win_rate", "expectancy_r", "p_edge", "profit_factor"}
    assert out["win_rate"]["n"] == 2
