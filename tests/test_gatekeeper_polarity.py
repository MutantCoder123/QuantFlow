"""Whale-CVD polarity check.

The gatekeeper tested the SIGN of the cumulative whale_cvd_ema_1h level, not
a change in it. For a net-selling name (cumulative whale CVD negative all
day, which is routine) that force-closed any held long the very next
gatekeeper tick regardless of what price was doing, burning an LLM call at
confidence 9/10. The fix compares the change since entry, normalised by
average daily volume so the threshold is comparable across a Rs.20 stock and
a Rs.10,000 one.
"""
import pytest
from freezegun import freeze_time

from intraday_gatekeeper import IntradayGatekeeper as G

# The gatekeeper's forced-square-off check computes its own IST time from
# datetime.utcnow() independently of pipeline_guard.is_market_open(), so
# these tests must freeze wall-clock time within market hours or they become
# time-of-day dependent (this UTC value is 14:30 IST -- 09:00 + 5:30).
_MARKET_HOURS = "2026-09-09 09:00:00"

BASE_STRUCT = {
    "1_live_microstructure": {"flow_divergence_state": "EQUILIBRIUM_CHOP"},
    "math_setup": {},
    "market_regime": {"current_regime": "TREND_EXPANSION", "session_phase": "MORNING_SESSION"},
}


def _pos(**kw):
    p = {"direction": "Long", "entry_price": 100.0, "entry_timestamp": 0,
         "stoploss": 90.0, "whale_cvd_at_entry": -500_000.0}
    p.update(kw)
    return p


@pytest.fixture(autouse=True)
def market_open(monkeypatch):
    monkeypatch.setattr("pipeline_guard.is_market_open", lambda: True)
    with freeze_time(_MARKET_HOURS):
        yield


def test_persistently_negative_whale_cvd_does_not_force_close():
    """A long in a net-selling name must not be closed merely because the
    cumulative level is below zero -- it always is, for such a name."""
    res = G.evaluate(BASE_STRUCT,
                     {"whale_cvd_ema_1h": -480_000.0, "adv_shares": 5_000_000.0},
                     {"position": _pos()}, ltp=101.0)
    assert res["Action"] != "Close"


def test_genuine_adverse_flip_does_close():
    """A large adverse move since entry (here: -1.5M shares against a 5M ADV,
    i.e. 30% of ADV, well over the 3% threshold) is a real flip."""
    res = G.evaluate(BASE_STRUCT,
                     {"whale_cvd_ema_1h": -2_000_000.0, "adv_shares": 5_000_000.0},
                     {"position": _pos()}, ltp=101.0)
    assert res["Action"] == "Close"


def test_short_position_uses_the_mirrored_direction():
    pos = _pos(direction="Short", whale_cvd_at_entry=500_000.0, stoploss=110.0)
    res = G.evaluate(BASE_STRUCT,
                     {"whale_cvd_ema_1h": 2_000_000.0, "adv_shares": 5_000_000.0},
                     {"position": pos}, ltp=101.0)
    assert res["Action"] == "Close"


def test_missing_entry_baseline_defaults_to_zero_not_a_crash():
    pos = _pos()
    del pos["whale_cvd_at_entry"]
    res = G.evaluate(BASE_STRUCT,
                     {"whale_cvd_ema_1h": -480_000.0, "adv_shares": 5_000_000.0},
                     {"position": pos}, ltp=101.0)
    assert res["Action"] in ("Hold", "Close")  # must not raise


def test_zero_adv_does_not_divide_by_zero():
    res = G.evaluate(BASE_STRUCT,
                     {"whale_cvd_ema_1h": -2_000_000.0, "adv_shares": 0.0},
                     {"position": _pos()}, ltp=101.0)
    assert res["Action"] in ("Hold", "Close")  # must not raise
