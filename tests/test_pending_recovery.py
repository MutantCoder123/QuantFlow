"""SignalLedger._pending_signals was memory-only, so any signal younger than
its resolution horizon at shutdown stayed PENDING forever and was silently
excluded from every statistic after a restart (C-3d).
"""
from signal_ledger import SignalLedger


def _pending_record(sig_id, ts):
    return {"signal_id": sig_id, "timestamp": ts,
            "outcome": {"status": "PENDING"}}


def _resolved_record(sig_id, ts):
    return {"signal_id": sig_id, "timestamp": ts,
            "outcome": {"status": "RESOLVED"}}


def test_recover_pending_restores_only_unresolved_signals(monkeypatch):
    SignalLedger._pending_signals.clear()
    monkeypatch.setattr(SignalLedger, "load_all_signals", classmethod(
        lambda cls, last_n_days=30: [
            _pending_record("A", 100), _resolved_record("B", 90),
            _pending_record("C", 50)]))

    n = SignalLedger.recover_pending(days=2)

    assert n == 2
    assert set(SignalLedger._pending_signals.keys()) == {"A", "C"}
    a = SignalLedger._pending_signals["A"]
    mins = SignalLedger._measure_at()
    assert a["targets"] == {m: 100 + m * 60 for m in mins}
    assert all(v is False for v in a["resolved"].values())
    SignalLedger._pending_signals.clear()


def test_recover_pending_with_no_signals_is_a_noop(monkeypatch):
    SignalLedger._pending_signals.clear()
    monkeypatch.setattr(SignalLedger, "load_all_signals", classmethod(
        lambda cls, last_n_days=30: []))
    assert SignalLedger.recover_pending() == 0
    assert SignalLedger._pending_signals == {}
