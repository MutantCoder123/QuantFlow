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


def test_flushing_on_another_thread_while_ticks_arrive_loses_nothing(tmp_path):
    """2026-09-29: flush() on a worker thread built its frame from the buffer
    while the loop kept appending -- "Length of values (2918) does not match
    length of index (2929)" -- and the flush task died for the session."""
    import threading
    import pandas as pd
    from journal.tick_recorder import TickRecorder
    rec = TickRecorder(tmp_path, flush_n=10**9)
    n, errors = 60_000, []

    def producer():
        for i in range(n):
            rec.record("NSE_EQ|SAIL", i, 100.0, i, 0, 99.9, 100.1, 10, 12)

    def flusher(stop):
        while not stop.is_set():
            try:
                rec.flush()
            except Exception as e:          # the bug surfaced here
                errors.append(e)
    stop = threading.Event()
    fl = [threading.Thread(target=flusher, args=(stop,)) for _ in range(2)]
    for t in fl:
        t.start()
    producer()
    stop.set()
    for t in fl:
        t.join()
    rec.flush()
    assert errors == []
    got = pd.concat([pd.read_parquet(p) for p in tmp_path.rglob("*.parquet")])
    assert sorted(got["ts_ms"]) == list(range(n))            # every tick exactly once


def test_a_restarted_process_never_overwrites_the_parts_already_written(tmp_path):
    """2026-09-29: a restarted feed started again at ticks_000001 and
    overwrote the morning's files; the feature log did the same."""
    import time
    import pandas as pd
    from journal.feature_log import FeatureLog
    from journal.tick_recorder import TickRecorder
    first = TickRecorder(tmp_path)
    first.record("A", 1, 1.0, 0, 0, 0, 0, 0, 0)
    first.flush()
    time.sleep(0.002)
    second = TickRecorder(tmp_path)                    # the restart
    second.record("A", 2, 1.0, 0, 0, 0, 0, 0, 0)
    second.flush()
    files = sorted(tmp_path.rglob("ticks_*.parquet"))
    assert len(files) == 2
    assert [int(pd.read_parquet(f)["ts_ms"][0]) for f in files] == [1, 2]   # names sort in time order

    feats = tmp_path / "features"
    for n in (1, 2):
        log = FeatureLog(feats)
        log._buf.append({"n": n})
        log.flush()
        time.sleep(0.002)
    assert sorted(pd.concat(pd.read_parquet(f) for f in feats.rglob("*.parquet"))["n"]) == [1, 2]
