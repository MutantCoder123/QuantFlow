"""Task 1.5: label_outcome needs real bar highs/lows, but RollingStateEngine's
ltf_df lives only in the upstox_feed.py process, not in main.py (where
SignalLedger's resolver runs) — confirmed by tracing api_server.py's existing
`aiohttp.ClientSession().get("http://127.0.0.1:8001/state")` cross-process
poll, the same pattern this endpoint follows. build_bars_response is the
pure, testable half of the new /api/bars bridge endpoint.
"""
import pandas as pd
from data_services.upstox_feed import build_bars_response


def test_empty_or_missing_df_returns_no_bars():
    assert build_bars_response(None) == {"bars": []}
    assert build_bars_response(pd.DataFrame()) == {"bars": []}


def test_returns_the_tail_with_stringified_timestamps():
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-09-09 09:15", "2026-09-09 09:20", "2026-09-09 09:25"]),
        "open": [100.0, 101.0, 102.0], "high": [101.0, 102.0, 103.0],
        "low": [99.0, 100.0, 101.0], "close": [100.5, 101.5, 102.5],
        "volume": [1000.0, 2000.0, 3000.0], "oi": [0.0, 0.0, 0.0],
    })
    out = build_bars_response(df, n=2)
    assert len(out["bars"]) == 2
    assert out["bars"][0]["timestamp"] == "2026-09-09 09:20:00"
    assert out["bars"][-1]["close"] == 102.5
    assert "oi" not in out["bars"][0]        # only OHLCV+timestamp are needed
