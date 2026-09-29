"""Atomic JSON writes for files another process reads (core.fileio)."""
import json
import threading
import time

from core.fileio import write_json_atomic


def test_the_file_is_written_and_no_temp_files_are_left(tmp_path):
    p = tmp_path / "state.json"
    write_json_atomic(p, {"a": 1}, indent=2)
    write_json_atomic(p, {"a": 2})
    assert json.loads(p.read_text()) == {"a": 2}
    assert [f.name for f in tmp_path.iterdir()] == ["state.json"]


def test_a_reader_never_sees_a_torn_file(tmp_path):
    """2026-09-29: the feed read institutional_flow.json mid-rewrite and got
    "Expecting value: line 1 column 1"."""
    p = tmp_path / "flow.json"
    big = {"rows": list(range(20_000))}
    write_json_atomic(p, big)
    bad, stop = [], threading.Event()

    def reader():                                   # paced like the feed's mtime reload
        while not stop.is_set():
            try:
                with open(p, encoding="utf-8") as f:
                    json.load(f)
            except ValueError as e:
                bad.append(e)
            except PermissionError:                     # Windows, mid-swap: the real readers retry
                pass
            time.sleep(0.005)
    t = threading.Thread(target=reader, daemon=True)
    t.start()
    try:
        for i in range(40):
            write_json_atomic(p, {**big, "i": i})
    finally:
        stop.set()
        t.join(5)
    assert bad == []
