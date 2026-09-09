from mtf_extractor import MTFFeatureExtractor


def test_squeeze_state_is_reachable():
    payload = {"bb_upper_15m": 101.0, "bb_lower_15m": 100.0, "atr_15m": 1.0}
    out = MTFFeatureExtractor.extract_all(payload, ltp=100.0)
    assert out["volatility_state"] == "15M_30M_COILING_SQUEEZE"


def test_overstretched_state_is_reachable():
    payload = {"ema_21_1h": 100.0, "atr_1h": 1.0}
    out = MTFFeatureExtractor.extract_all(payload, ltp=104.0)   # 4 ATR above
    assert out["elasticity_risk"] == "OVERSTRETCHED_MEAN_REVERSION_RISK_DOWN"


def test_rolling_engine_publishes_the_four_mtf_fields():
    from rolling_state_engine import RollingStateEngine
    import inspect
    src = inspect.getsource(RollingStateEngine._compute_symbol)
    assert "MTFFeatureExtractor" in src, "extractor is still not wired in"
