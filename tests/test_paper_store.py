"""Append-only paper event log and state rebuild.

Recovery must be exact: replaying the log after a crash yields the same open
positions and closed trades, a duplicated event counts once, a torn last line
(crash mid-write) is skipped rather than fatal, and the latest marks restore
MAE/MFE for positions still open.
"""
from datetime import datetime

from paper.store import EventStore, rebuild

IST_0930 = datetime(2026, 9, 28, 9, 30).timestamp()


def _open(pid="P1", sym="SAIL", ts=IST_0930):
    return {"type": "OPEN", "ts": ts, "pos_id": pid, "symbol": sym, "side": "LONG",
            "qty": 100, "entry_price": 100.0, "stop": 98.0, "target": 104.0,
            "risk_amount": 200.0}


def _close(pid="P1", sym="SAIL", ts=IST_0930 + 600, net=150.0):
    return {"type": "CLOSE", "ts": ts, "pos_id": pid, "symbol": sym,
            "exit_price": 101.6, "reason": "TARGET", "net": net}


def test_append_then_load_round_trips_in_order(tmp_path):
    st = EventStore(tmp_path)
    a = st.append(_open())
    b = st.append(_close())
    got = st.load()
    assert [e["id"] for e in got] == [a["id"], b["id"]]
    assert got[0]["symbol"] == "SAIL"


def test_events_get_ids_and_land_in_the_ist_day_file(tmp_path):
    st = EventStore(tmp_path)
    e = st.append(_open())
    assert e["id"]
    assert (tmp_path / "events_2026-09-28.jsonl").exists()


def test_rebuild_open_and_closed(tmp_path):
    state = rebuild([_open("P1"), _open("P2", "IDEA"), _close("P1")])
    assert list(state.open) == ["P2"]
    assert [t["pos_id"] for t in state.closed] == ["P1"]
    assert state.closed[0]["symbol"] == "SAIL"
    assert state.closed[0]["entry_price"] == 100.0        # OPEN fields merged into the trade


def test_duplicate_events_count_once():
    o, c = _open(), _close()
    o["id"], c["id"] = "e1", "e2"
    state = rebuild([o, dict(o), c, dict(c)])
    assert len(state.closed) == 1 and not state.open


def test_a_torn_last_line_is_skipped_not_fatal(tmp_path):
    st = EventStore(tmp_path)
    st.append(_open())
    with open(tmp_path / "events_2026-09-28.jsonl", "a", encoding="utf-8") as f:
        f.write('{"type": "CLOSE", "pos_id": "P1", "ts": 17')      # crash mid-write
    got = st.load()
    assert len(got) == 1 and st.skipped_lines == 1


def test_latest_marks_restore_mae_mfe_for_open_positions():
    snap = {"type": "EQUITY", "ts": IST_0930 + 60,
            "marks": {"P1": {"last": 99.1, "mae_r": -0.45, "mfe_r": 0.2, "ts": IST_0930 + 60}}}
    later = {"type": "EQUITY", "ts": IST_0930 + 120,
             "marks": {"P1": {"last": 101.0, "mae_r": -0.45, "mfe_r": 0.5, "ts": IST_0930 + 120}}}
    state = rebuild([_open(), snap, later])
    assert state.open["P1"]["mae_r"] == -0.45 and state.open["P1"]["mfe_r"] == 0.5
    assert state.open["P1"]["last"] == 101.0


def test_rejections_and_settings_changes_are_kept():
    rej = {"type": "REJECT", "ts": IST_0930, "symbol": "IDEA", "reason": "CLUSTER_LIMIT_TELECOM"}
    chg = {"type": "SETTINGS", "ts": IST_0930, "old": {"capital": 1e6}, "new": {"capital": 5e5}}
    state = rebuild([rej, chg])
    assert state.rejections[0]["reason"] == "CLUSTER_LIMIT_TELECOM"
    assert state.settings_changes[0]["new"] == {"capital": 5e5}


def test_load_spans_multiple_days(tmp_path):
    st = EventStore(tmp_path)
    st.append(_open(ts=datetime(2026, 9, 25, 10, 0).timestamp()))
    st.append(_open("P2", ts=IST_0930))
    assert len(st.load()) == 2
