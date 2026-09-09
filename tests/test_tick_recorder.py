import pandas as pd
from journal.tick_recorder import TickRecorder


def test_flush_writes_parquet_with_expected_schema(tmp_path):
    r = TickRecorder(tmp_path, flush_n=1_000_000)
    for i in range(5):
        r.record("NSE_EQ|SAIL", 1_757_000_000_000 + i * 250, 132.5 + i * 0.05,
                 1_000_000 + i * 500, 0.0, 132.45, 132.55, 900, 1100)
    out = r.flush()
    assert out is not None and out.exists()
    df = pd.read_parquet(out)
    assert len(df) == 5
    assert list(df.columns) == ["token", "ts_ms", "ltp", "vtt", "oi",
                                "bid1", "ask1", "bid_qty", "ask_qty"]
    assert df["vtt"].iloc[-1] == 1_002_000


def test_autoflush_at_threshold(tmp_path):
    r = TickRecorder(tmp_path, flush_n=3)
    for i in range(3):
        r.record("T", i, 1.0, i, 0.0, 0.9, 1.1, 1, 1)
    assert list(tmp_path.rglob("*.parquet")), "auto-flush did not fire"


def test_flush_on_empty_buffer_is_a_noop(tmp_path):
    assert TickRecorder(tmp_path).flush() is None
