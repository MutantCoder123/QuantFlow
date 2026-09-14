"""Horizon coherence (improved §4.4): one config value governs the entry
cutoff, the ledger's measurement points, and the prompt. The prompt used to
claim 2-6h while the ledger graded at 30/60m and square-off was 15:20.
"""
import pytest
from freezegun import freeze_time

from intraday_gatekeeper import IntradayGatekeeper as G

_AUTHORISED_SETUP = {
    "math_setup": {"setup_rejected": False, "composite_score": 0.5,
                   "directional_bias": "LONG",
                   "expectancy_matrix": {"statistical_edge": 0.3},
                   "execution_geometry": {}},
    "market_regime": {"current_regime": "TREND_EXPANSION", "session_phase": "POWER_HOUR"},
    "1_live_microstructure": {},
}


@pytest.fixture(autouse=True)
def market_open(monkeypatch):
    monkeypatch.setattr("pipeline_guard.is_market_open", lambda: True)
    yield


def test_no_entry_after_the_cutoff():
    with freeze_time("2026-09-09 14:30:00+05:30"):
        res = G.evaluate(_AUTHORISED_SETUP, {}, {}, ltp=100.0)
    assert res["llm_authorized"] is False
    assert "ENTRY_CUTOFF" in res.get("math_rejection", "")


def test_entry_still_allowed_before_the_cutoff():
    with freeze_time("2026-09-09 11:00:00+05:30"):
        res = G.evaluate(_AUTHORISED_SETUP, {}, {}, ltp=100.0)
    assert res["llm_authorized"] is True
    assert "ENTRY_CUTOFF" not in res.get("math_rejection", "")
