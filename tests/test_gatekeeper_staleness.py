"""Stale-feed shield (A-12): a dropped WebSocket stream used to be silent and
permanent -- the UI kept showing the last frozen frame and the gatekeeper
kept acting on it. RollingStateEngine now publishes data_age_s per token and
the gatekeeper refuses to act once it exceeds 15s.
"""
import pytest
from freezegun import freeze_time

from intraday_gatekeeper import IntradayGatekeeper as G

_MARKET_HOURS = "2026-09-09 09:00:00"

BASE_STRUCT = {
    "1_live_microstructure": {"flow_divergence_state": "EQUILIBRIUM_CHOP"},
    "math_setup": {"setup_rejected": False, "composite_score": 0.5,
                   "directional_bias": "LONG",
                   "expectancy_matrix": {"statistical_edge": 0.3},
                   "execution_geometry": {}},
    "market_regime": {"current_regime": "TREND_EXPANSION", "session_phase": "MORNING_SESSION"},
}


@pytest.fixture(autouse=True)
def market_open(monkeypatch):
    monkeypatch.setattr("pipeline_guard.is_market_open", lambda: True)
    with freeze_time(_MARKET_HOURS):
        yield


def test_fresh_data_is_not_blocked_by_the_stale_shield():
    res = G.evaluate(BASE_STRUCT, {"data_age_s": 2.0}, {}, ltp=100.0)
    assert "STALE_DATA" not in res.get("math_rejection", "")


def test_stale_data_is_rejected_and_not_llm_authorized():
    res = G.evaluate(BASE_STRUCT, {"data_age_s": 42.0}, {}, ltp=100.0)
    assert res["llm_authorized"] is False
    assert res["math_rejection"] == "STALE_DATA_42s"
    assert res["Action"] == "Wait"


def test_missing_age_field_is_treated_as_fresh():
    res = G.evaluate(BASE_STRUCT, {}, {}, ltp=100.0)
    assert "STALE_DATA" not in res.get("math_rejection", "")
