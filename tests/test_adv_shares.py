"""_compute_symbol must publish adv_shares so the A-6 whale-flip check has a
real average-daily-volume denominator instead of falling back to its safe
floor for every symbol.
"""
import pandas as pd

from diagnostic_ui import TerminalDashboard
from rolling_state_engine import RollingStateEngine

TOKEN = "NSE_EQ|ADVTEST"


def _engine(n_bars: int, per_bar_volume: float):
    idx = pd.date_range("2026-08-01 09:15", periods=n_bars, freq="5min")
    df = pd.DataFrame({
        "timestamp": idx, "open": 100.0, "high": 101.0, "low": 99.0,
        "close": 100.0, "volume": per_bar_volume, "oi": 0.0,
    })
    eng = RollingStateEngine.__new__(RollingStateEngine)
    eng.dfs = {TOKEN: {"ltf_df": df, "htf_df": pd.DataFrame()}}
    eng.watchlist = {TOKEN: {"symbol": "ADVTEST"}}
    eng.phantom_candles = {}
    eng._failures = {}
    return eng


def _phantom():
    return {"timestamp": pd.Timestamp("2026-09-09 09:20"), "open": 100.0,
            "high": 100.0, "low": 100.0, "close": 100.0, "volume": 0.0,
            "oi": 0.0, "microstructure": {}}


def test_adv_shares_is_20_session_average():
    # 1500 bars (= 20 sessions x 75 5-min bars) at 1000 shares/bar -> 1.5M/day
    eng = _engine(n_bars=1500, per_bar_volume=1000.0)
    eng._compute_symbol(TOKEN, _phantom())
    payload = TerminalDashboard.active_states[TOKEN]
    assert payload["adv_shares"] == 1_500_000.0 / 20.0


def test_adv_shares_defaults_to_zero_with_insufficient_history():
    eng = _engine(n_bars=10, per_bar_volume=1000.0)
    eng._compute_symbol(TOKEN, _phantom())
    payload = TerminalDashboard.active_states[TOKEN]
    assert payload["adv_shares"] == 0.0
