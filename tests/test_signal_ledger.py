"""SignalLedger.record_signal must survive a held position.

When a user holds a position, build_structured_payload() deliberately sets
execution_geometry and expectancy_matrix to None (to hide new-entry
suggestions during an active trade). `.get(key, default)` returns the STORED
None in that case -- the default only applies when the key is absent -- so
`None.get("padded_stop")` raised AttributeError. Because record_signal is only
reached on CLOSE_EXISTING/REVERSE_POSITION directives, which by definition
require an existing position, this meant position-management signals were
never recorded at all.
"""
import pytest

from signal_ledger import SignalLedger


@pytest.fixture(autouse=True)
def clear_pending():
    SignalLedger._pending_signals.clear()
    yield
    SignalLedger._pending_signals.clear()


def test_record_signal_survives_null_geometry(monkeypatch):
    monkeypatch.setattr(SignalLedger, "_append_to_log", classmethod(lambda cls, r, d=None: None))

    SignalLedger.record_signal(
        symbol="SAIL",
        execution_ticket={"verdict": "CONFIRM", "action_directive": "CLOSE_EXISTING"},
        math_setup={"composite_score": 0.31,
                    "execution_geometry": None,
                    "expectancy_matrix": None},
        market_regime={"current_regime": "TREND_EXPANSION", "session_phase": "POWER_HOUR"},
        ltp=132.5,
    )

    assert len(SignalLedger._pending_signals) == 1
    snap = next(iter(SignalLedger._pending_signals.values()))["record"]["signal_snapshot"]
    assert snap["padded_stop"] == 0.0
    assert snap["calculated_target"] == 0.0
    assert snap["calculated_entry"] == 0.0
    assert snap["implied_probability"] is None


def test_record_signal_still_captures_real_geometry(monkeypatch):
    """Regression guard: the fix must not silently swallow real geometry."""
    monkeypatch.setattr(SignalLedger, "_append_to_log", classmethod(lambda cls, r, d=None: None))

    SignalLedger.record_signal(
        symbol="INFY",
        execution_ticket={"verdict": "CONFIRM", "action_directive": "EXECUTE_LONG"},
        math_setup={"composite_score": 0.42,
                    "execution_geometry": {"padded_stop": 1500.0, "calculated_target": 1600.0,
                                           "calculated_entry": 1550.0},
                    "expectancy_matrix": {"implied_probability": 0.71}},
        market_regime={"current_regime": "TREND_EXPANSION", "session_phase": "MORNING_SESSION"},
        ltp=1550.0,
    )

    snap = next(iter(SignalLedger._pending_signals.values()))["record"]["signal_snapshot"]
    assert snap["padded_stop"] == 1500.0
    assert snap["calculated_target"] == 1600.0
    assert snap["calculated_entry"] == 1550.0
    assert snap["implied_probability"] == 0.71
