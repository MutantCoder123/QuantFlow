"""process_tick must forward every tick to the TickRecorder attached to the
engine (Task 1.2) — the live-session verification in the plan (killing the
feed for 2 minutes and inspecting the parquet output) needs a running market
and is not reproducible in a unit test, so this locks in the wiring instead:
every tick that reaches process_tick is captured with the correct fields,
and an engine with no recorder attached (as every __new__-bypass test fixture
in this suite constructs it) does not crash.
"""
import pandas as pd
from rolling_state_engine import RollingStateEngine

TOKEN = "NSE_EQ|RECTEST"


class _FakeRecorder:
    def __init__(self):
        self.calls = []

    def record(self, token, ts_ms, ltp, vtt, oi, bid1, ask1, bid_qty, ask_qty):
        self.calls.append(dict(token=token, ts_ms=ts_ms, ltp=ltp, vtt=vtt, oi=oi,
                               bid1=bid1, ask1=ask1, bid_qty=bid_qty, ask_qty=ask_qty))


def _engine():
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-09-09 09:15:00"]),
        "open": [100.0], "high": [100.0], "low": [100.0],
        "close": [100.0], "volume": [0.0], "oi": [0.0],
    })
    eng = RollingStateEngine.__new__(RollingStateEngine)
    eng.dfs = {TOKEN: {"ltf_df": df, "htf_df": pd.DataFrame()}}
    eng.watchlist = {TOKEN: {"symbol": "RECTEST"}}
    eng.phantom_candles = {}
    return eng


def test_every_tick_is_forwarded_to_the_attached_recorder():
    eng = _engine()
    eng.recorder = _FakeRecorder()
    eng.process_tick(token=TOKEN, timestamp_ms=1_757_000_000_000, price=132.5,
                     volume=1_000_000.0, oi=0.0,
                     bids=[{"quantity": 900, "price": 132.45}],
                     asks=[{"quantity": 1100, "price": 132.55}])
    assert eng.recorder.calls == [dict(
        token=TOKEN, ts_ms=1_757_000_000_000, ltp=132.5, vtt=1_000_000.0, oi=0.0,
        bid1=132.45, ask1=132.55, bid_qty=900, ask_qty=1100)]


def test_missing_book_records_zeros_not_a_crash():
    eng = _engine()
    eng.recorder = _FakeRecorder()
    eng.process_tick(token=TOKEN, timestamp_ms=1, price=100.0, volume=0.0, oi=0.0,
                     bids=[], asks=[])
    assert eng.recorder.calls[0]["bid1"] == 0.0 and eng.recorder.calls[0]["ask_qty"] == 0


def test_engine_with_no_recorder_attached_does_not_crash():
    """Every __new__-bypass fixture elsewhere in this suite constructs an
    engine without a recorder — the class-level default must make that safe."""
    eng = _engine()
    eng.process_tick(token=TOKEN, timestamp_ms=1, price=100.0, volume=0.0, oi=0.0,
                     bids=[{"quantity": 1, "price": 99.9}],
                     asks=[{"quantity": 1, "price": 100.1}])
