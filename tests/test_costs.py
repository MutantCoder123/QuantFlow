from core.costs import round_trip_cost_pct, net_reward, load_costs


def test_round_trip_cost_is_within_the_expected_band():
    """Indian intraday equity: ~0.05-0.10% round trip."""
    c = round_trip_cost_pct(entry=100.0, exit_px=101.0, cfg=load_costs())
    assert 0.03 < c < 0.15, c


def test_cost_scales_with_notional_under_the_brokerage_cap():
    cfg = load_costs()
    assert round_trip_cost_pct(10.0, 10.1, cfg) > round_trip_cost_pct(5000.0, 5050.0, cfg)


def test_net_reward_is_strictly_less_than_gross():
    cfg = load_costs()
    assert net_reward(1.0, 100.0, 101.0, spread=0.05, cfg=cfg) < 1.0
