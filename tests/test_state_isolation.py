from regime_manager import RegimeManagerRegistry
from conviction_scorer import ConvictionScorerRegistry

PAYLOAD = {"current_time": "10:30 am", "ltp": 100.0, "timestamp": 1,
           "1_live_microstructure": {"flow_divergence_state": "EQUILIBRIUM_CHOP",
                                     "volume_regime": "NORMAL_DRIFT",
                                     "fractal_alignment": "CONFLICTING_CHOP",
                                     "elasticity_risk": "EQUILIBRIUM",
                                     "kinetic_divergence": "MOMENTUM_CONFIRMED",
                                     "volatility_state": "NORMAL_RANGING"},
           "2_derivatives_matrix_52w": {}, "3_local_structural_edge_20d": {}}


def test_peek_does_not_grow_the_regime_memory(monkeypatch):
    monkeypatch.setattr("pipeline_guard.is_market_open", lambda: True)
    m = RegimeManagerRegistry.get_or_create("PEEKTEST")
    m.determine_regime(dict(PAYLOAD))
    n = len(m.memory.buffer)
    for _ in range(50):
        m.peek_regime()
    assert len(m.memory.buffer) == n, "peek mutated the hysteresis buffer"


def test_peek_does_not_advance_epoch_counter(monkeypatch):
    monkeypatch.setattr("pipeline_guard.is_market_open", lambda: True)
    m = RegimeManagerRegistry.get_or_create("EPOCHTEST")
    m.determine_regime(dict(PAYLOAD))
    before = m.epochs_in_regime
    for _ in range(20):
        m.peek_regime()
    assert m.epochs_in_regime == before


def test_scoring_without_advance_does_not_count_polarity_flips():
    s = ConvictionScorerRegistry.get_or_create("FLIPTEST")
    s.previous_bias = "LONG"
    before = s.polarity_flips_today
    for _ in range(10):
        s.score_setup({"market_regime": {"current_regime": "TREND_EXPANSION"},
                       "1_live_microstructure": {"flow_divergence_state":
                                                 "MOMENTUM_CONFIRMED_BEARISH"}},
                      {"ltp": 100.0}, advance_state=False)
    assert s.polarity_flips_today == before
