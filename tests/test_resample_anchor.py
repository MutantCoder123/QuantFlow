import pandas as pd
from technical_engine import MathEngine


def test_hourly_bars_start_at_the_session_open():
    idx = pd.date_range("2026-09-09 09:15", "2026-09-09 15:30", freq="5min")
    df = pd.DataFrame({"timestamp": idx, "open": 100.0, "high": 101.0,
                       "low": 99.0, "close": 100.5, "volume": 1000.0})
    omni = MathEngine.generate_omni_dataframes(df)
    first = omni["1h"].index[0]
    assert (first.hour, first.minute) == (9, 15), f"1h bar anchored at {first}"


def test_four_hour_bars_reanchor_each_session():
    """A 4h resample anchored at 09:15 tiles cleanly across days (24h / 4h =
    6), so every session's first 4h bar also starts at 09:15."""
    idx = pd.date_range("2026-09-09 09:15", "2026-09-11 15:30", freq="5min")
    df = pd.DataFrame({"timestamp": idx, "open": 100.0, "high": 101.0,
                       "low": 99.0, "close": 100.5, "volume": 1000.0})
    omni = MathEngine.generate_omni_dataframes(df)
    starts = {(t.hour, t.minute) for t in omni["4h"].index}
    assert (9, 15) in starts
    assert (0, 0) not in starts        # never midnight-anchored
