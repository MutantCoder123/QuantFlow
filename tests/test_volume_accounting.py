"""Volume accounting.

Upstox sends `vtt` -- volume traded today, a CUMULATIVE counter. Two subsystems
previously disagreed about what it meant: MicrostructureEngine differenced it
correctly, while RollingStateEngine.process_tick added the running total on
every tick and then committed that corrupted phantom into ltf_df every 5
minutes, permanently poisoning the time-of-day volume baseline.
"""
import pandas as pd
import pytest

from microstructure_engine import MicrostructureEngine as M
from rolling_state_engine import RollingStateEngine

TOKEN = "NSE_EQ|TESTSYM"
_STATE_DICTS = ("cvd_state", "vol_profile_state", "session_vwap_state",
                "whale_cvd_state", "whale_cvd_history", "last_vtt_state",
                "last_bba_state", "session_date")


@pytest.fixture(autouse=True)
def clean_state():
    for name in _STATE_DICTS:
        if hasattr(M, name):
            getattr(M, name).clear()
    yield


def _tick(vtt, price=100.0):
    return M.generate_microstructure_payload({
        "token": TOKEN, "price": price, "volume": vtt,
        "bids": [{"quantity": 10, "price": price - 0.05}],
        "asks": [{"quantity": 10, "price": price + 0.05}],
    })


def test_first_tick_establishes_baseline_with_zero_volume():
    assert _tick(1_000_000.0)["tick_volume"] == 0.0


def test_tick_volume_is_the_delta():
    _tick(1_000_000.0)
    assert _tick(1_002_500.0)["tick_volume"] == 2_500.0
    assert _tick(1_003_000.0)["tick_volume"] == 500.0


def test_large_opening_range_increment_is_not_discarded():
    """Regression: the old guard zeroed any increment > 50% of cumulative,
    which is routine in the first 30 minutes of a session and preferentially
    destroyed exactly the volume spikes TIME_ADJUSTED_SHOCK exists to detect."""
    _tick(1_000_000.0)
    assert _tick(3_000_000.0)["tick_volume"] == 2_000_000.0


def test_counter_going_backwards_yields_zero_not_negative():
    """A reconnect or session rollover resets vtt; never emit negative volume."""
    _tick(5_000_000.0)
    assert _tick(1_000.0)["tick_volume"] == 0.0


# --------------------------------------------------------------------------
# Phantom candle
# --------------------------------------------------------------------------

def _engine():
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-09-09 09:15:00"]),
        "open": [100.0], "high": [100.0], "low": [100.0],
        "close": [100.0], "volume": [50_000.0], "oi": [0.0],
    })
    eng = RollingStateEngine.__new__(RollingStateEngine)   # bypass cache hydration
    eng.dfs = {TOKEN: {"ltf_df": df, "htf_df": pd.DataFrame()}}
    eng.watchlist = {TOKEN: {"symbol": "TESTSYM"}}
    eng.phantom_candles = {}
    eng.recorder = None
    eng._failures = {}
    eng.last_tick_ts = {}
    return eng


def _push(eng, ms, vtt, price=100.0):
    eng.process_tick(token=TOKEN, timestamp_ms=ms, price=price, volume=vtt, oi=0.0,
                     bids=[{"quantity": 10, "price": price - 0.05}],
                     asks=[{"quantity": 10, "price": price + 0.05}])


def test_phantom_volume_accumulates_deltas_not_cumulative_total():
    eng = _engine()
    base = int(pd.Timestamp("2026-09-09 09:20:00").timestamp() * 1000)
    for off, vtt in ((0, 1_000_000.0), (1_000, 1_002_500.0), (2_000, 1_003_000.0)):
        _push(eng, base + off, vtt)
    # deltas: 0 (baseline) + 2500 + 500
    assert eng.phantom_candles[TOKEN]["volume"] == 3_000.0


def test_committed_bar_carries_realistic_volume():
    """The 5-minute commit must not write a multi-million-share bar for a
    stock whose real 5-minute volume is a few thousand."""
    eng = _engine()
    b1 = int(pd.Timestamp("2026-09-09 09:20:00").timestamp() * 1000)
    b2 = int(pd.Timestamp("2026-09-09 09:25:00").timestamp() * 1000)
    for off, vtt in ((0, 1_000_000.0), (1_000, 1_004_000.0)):
        _push(eng, b1 + off, vtt)
    _push(eng, b2, 1_005_000.0)          # boundary cross -> commits the first bar

    committed = eng.dfs[TOKEN]["ltf_df"].iloc[-1]
    assert committed["volume"] == 4_000.0
    assert "microstructure" not in eng.dfs[TOKEN]["ltf_df"].columns
