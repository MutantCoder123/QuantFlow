"""A new trade only on complete data (2026-10-01): stocks added that week
were scored with no 52-week options history, their derivatives component
silently neutral. Exits are never gated on it."""
import math

import pytest
from freezegun import freeze_time

from core.data_readiness import missing, ready
from intraday_gatekeeper import IntradayGatekeeper as G
from test_paper_broker_open import make, sig

COMPLETE = {"iv_percentile_52w": 98.8, "pcr_percentile_52w": 6.3, "rolling_20d_poc_price": 1282.6,
            "rolling_20d_value_area_high": 1333.0, "rolling_20d_value_area_low": 1180.0,
            "atr_15m": 3.76, "atr_1h": 5.95, "ema_21_1h": 1196.2, "session_vwap": 1180.4,
            "prev_close": 1226.0, "adv_shares": 11_676_160.7, "data_age_s": 0.4}


def test_a_complete_payload_is_ready():
    assert ready(COMPLETE) and missing(COMPLETE) == []


def test_no_options_history_is_not_ready_and_says_so():
    p = dict(COMPLETE, iv_percentile_52w=math.nan, pcr_percentile_52w=None)       # BANKINDIA today
    assert missing(p) == ["52-week IV history", "52-week PCR history"]


def test_a_zero_percentile_is_a_value_but_a_zero_price_or_atr_is_not():
    assert ready(dict(COMPLETE, iv_percentile_52w=0.0))
    assert missing(dict(COMPLETE, atr_15m=0.0, rolling_20d_value_area_low=0)) == ["20-day value area", "15-minute ATR"]
    assert not ready(None)


# -- the gatekeeper ------------------------------------------------------------------
STRUCT = {"1_live_microstructure": {}, "math_setup": {"setup_rejected": False, "composite_score": 0.5,
          "directional_bias": "LONG", "expectancy_matrix": {}, "execution_geometry": {}},
          "market_regime": {"current_regime": "TREND_EXPANSION", "session_phase": "MORNING_SESSION"}}


@pytest.fixture
def mid_morning(monkeypatch):
    monkeypatch.setattr("pipeline_guard.is_market_open", lambda: True)
    with freeze_time("2026-10-01 05:00:00"):                   # 10:30 IST
        yield


def test_an_incomplete_stock_is_never_escalated(mid_morning):
    res = G.evaluate(STRUCT, dict(COMPLETE, iv_percentile_52w=None), {}, ltp=100.0)
    assert res["math_rejection"] == "DATA_INCOMPLETE" and res["llm_authorized"] is False
    assert res["data_missing"] == ["52-week IV history"]


def test_a_complete_stock_is_not_held_for_data(mid_morning):
    assert G.evaluate(STRUCT, COMPLETE, {}, ltp=100.0).get("math_rejection") != "DATA_INCOMPLETE"


def test_an_open_position_is_still_managed_on_incomplete_data(mid_morning):
    pos = {"position": {"direction": "Long", "entry_price": 100.0, "stoploss": 98.0, "target": 104.0}}
    res = G.evaluate(STRUCT, dict(COMPLETE, iv_percentile_52w=None), pos, ltp=100.5)
    assert res.get("math_rejection") != "DATA_INCOMPLETE"


# -- the paper account --------------------------------------------------------------
def test_the_paper_account_refuses_partial_data(tmp_path):
    b, _, _ = make(tmp_path)
    assert b.on_signal(sig(data_missing=["52-week IV history"]))["reason"] == "DATA_INCOMPLETE"
    assert b.on_signal(sig(data_missing=[]))["type"] == "OPEN"
