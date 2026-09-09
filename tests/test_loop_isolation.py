"""calculate_technicals_loop must not let one bad symbol starve the rest.

The try/except previously wrapped the whole for-loop rather than each
iteration (try opened right after `while True:`, except closed right before
the trailing `await asyncio.sleep(1.5)`), so an unhandled exception on symbol
k aborted symbols k+1..n for that entire 1.5s cycle. If the fault is
deterministic, those symbols are starved permanently while the log shows one
repeating line.
"""
import pandas as pd
import pytest

from rolling_state_engine import RollingStateEngine


def _bar_df():
    return pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-09-09 09:15"]),
        "open": [1.0], "high": [1.0], "low": [1.0],
        "close": [1.0], "volume": [1.0], "oi": [0.0],
    })


def _phantom():
    return {"timestamp": pd.Timestamp("2026-09-09 09:20"), "open": 1.0,
            "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0,
            "oi": 0.0, "microstructure": {}}


def _engine():
    eng = RollingStateEngine.__new__(RollingStateEngine)
    eng.dfs = {"BAD": {"ltf_df": _bar_df(), "htf_df": pd.DataFrame()},
               "GOOD": {"ltf_df": _bar_df(), "htf_df": pd.DataFrame()}}
    eng.watchlist = {"BAD": {"symbol": "BAD"}, "GOOD": {"symbol": "GOOD"}}
    eng.phantom_candles = {"BAD": _phantom(), "GOOD": _phantom()}
    eng._failures = {}
    return eng


def _patch_math_engine_noop(monkeypatch):
    """generate_signal_payload has its own inner try/except in _compute_symbol
    (return-on-failure), so a fault there was never the A-13 bug -- it never
    escapes to run_one_cycle. The ~130 lines AFTER that block (building
    final_payload's remaining fields, calling TerminalDashboard.update_state)
    had zero protection in the original code; a fault there propagated to the
    OUTER except, which aborted every symbol after the failing one for that
    whole 1.5s cycle. Faults are injected there instead, to test the real gap.
    """
    from technical_engine import MathEngine
    monkeypatch.setattr(MathEngine, "generate_signal_payload",
                        staticmethod(lambda df, htf_df, token, index_df=None: {"token": token}))


@pytest.mark.asyncio
async def test_one_bad_symbol_does_not_starve_the_rest(monkeypatch):
    eng = _engine()
    _patch_math_engine_noop(monkeypatch)
    seen = []

    from diagnostic_ui import TerminalDashboard
    original_update = TerminalDashboard.update_state

    def fake_update_state(token, payload):
        seen.append(token)
        if token == "BAD":
            raise ValueError("synthetic failure downstream of MathEngine")
        return original_update(token, payload)

    monkeypatch.setattr(TerminalDashboard, "update_state", staticmethod(fake_update_state))

    await eng.run_one_cycle()

    assert "GOOD" in seen, "GOOD was starved by BAD's failure"
    assert "BAD" in seen


@pytest.mark.asyncio
async def test_failure_count_is_tracked_per_symbol(monkeypatch):
    eng = _engine()
    _patch_math_engine_noop(monkeypatch)

    from diagnostic_ui import TerminalDashboard
    monkeypatch.setattr(TerminalDashboard, "update_state",
                        staticmethod(lambda token, payload: (_ for _ in ()).throw(
                            ValueError("synthetic failure"))))

    await eng.run_one_cycle()
    await eng.run_one_cycle()

    assert eng._failures.get("BAD", 0) == 2
    assert eng._failures.get("GOOD", 0) == 2


@pytest.mark.asyncio
async def test_failure_count_resets_after_a_successful_cycle(monkeypatch):
    eng = _engine()
    _patch_math_engine_noop(monkeypatch)
    call_count = {"n": 0}

    from diagnostic_ui import TerminalDashboard
    original_update = TerminalDashboard.update_state

    def fails_once_then_succeeds(token, payload):
        if token == "BAD" and call_count["n"] == 0:
            call_count["n"] += 1
            raise ValueError("synthetic failure")
        return original_update(token, payload)

    monkeypatch.setattr(TerminalDashboard, "update_state", staticmethod(fails_once_then_succeeds))

    await eng.run_one_cycle()
    assert eng._failures.get("BAD", 0) == 1

    await eng.run_one_cycle()
    assert "BAD" not in eng._failures
