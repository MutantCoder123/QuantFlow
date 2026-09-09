"""Task 1.5 (C-3b/c): the resolver used to sample current_ltp once every 60s
against stop/target — any touch-and-recover inside a minute was invisible,
and a +0.001% move at the 60m mark counted as a win. SignalLedger._resolve_one
is the pure per-signal step (no network, no event loop) that replaces that
with bar-accurate label_outcome() calls; the live async loop around it just
fetches bars and calls this once per pending signal per tick.
"""
import pandas as pd
from signal_ledger import SignalLedger

ENTRY_TS = int(pd.Timestamp("2026-09-09 10:00").timestamp())


def _bars():
    return pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-09-09 10:00", "2026-09-09 10:05",
                                      "2026-09-09 10:10", "2026-09-09 10:35"]),
        "open":  [100.0, 100.5, 98.5, 97.0],
        "high":  [100.8, 101.0, 98.8, 97.5],
        "low":   [ 99.9, 100.1, 97.8, 96.5],
        "close": [100.5, 100.2, 98.4, 97.2],
    })


def _record():
    return {"symbol": "SAIL", "timestamp": ENTRY_TS,
            "signal_snapshot": {"ltp_at_signal": 100.0, "bias": "LONG",
                               "padded_stop": 98.0, "calculated_target": 110.0},
            "outcome": {"status": "PENDING", "hit_stop": False, "hit_target": False}}


def _state(rec):
    return {"record": rec, "target_30m": ENTRY_TS + 1800, "target_60m": ENTRY_TS + 3600,
            "resolved_30m": False, "resolved_60m": False}


def test_intrabar_stop_touch_resolves_early_at_the_30m_checkpoint():
    """The bar at 10:10 dips to a low of 97.8, touching the 98.0 stop within
    the 30-minute window; a 60s-LTP sampler could easily have missed it."""
    rec = _record()
    st = _state(rec)
    now_ts = ENTRY_TS + 1800
    updated = SignalLedger._resolve_one(rec, st, now_ts, _bars())
    assert updated is True
    assert rec["outcome"]["hit_stop"] is True
    assert rec["outcome"]["status"] == "RESOLVED_EARLY"
    assert st["resolved_30m"] is True and st["resolved_60m"] is True


def test_tiny_positive_move_below_cost_floor_is_not_directionally_correct():
    rec = _record()
    rec["signal_snapshot"]["padded_stop"] = 0.0     # disable stop so it can't hit early
    flat = _bars().copy()
    for c in ("open", "high", "low", "close"):
        flat[c] = 100.02
    st = _state(rec)
    updated = SignalLedger._resolve_one(rec, st, ENTRY_TS + 3600, flat)
    assert updated is True
    assert rec["outcome"]["pnl_60m_pct"] > 0
    assert rec["outcome"]["directional_correct_60m"] is False
    assert rec["outcome"]["status"] == "RESOLVED"


def test_before_any_checkpoint_is_a_noop():
    rec = _record()
    st = _state(rec)
    assert SignalLedger._resolve_one(rec, st, ENTRY_TS + 60, _bars()) is False
