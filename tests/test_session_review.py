"""Session review aggregation + GET /api/session/review (Task 5.6).

The load-bearing case is the honest one: a session that emitted signals but
has resolved none of them at the primary horizon must be reported as "not
measured yet", never as a 0% win rate.
"""
import datetime

from fastapi.testclient import TestClient

import api_server
from performance_analyzer import PerformanceAnalyzer

client = TestClient(api_server.app)

PRIMARY = PerformanceAnalyzer._primary_minute()


def _sig(symbol="SAIL", date="2026-09-11", regime="TREND_EXPANSION",
         resolved=None, pnl=0.0, correct=None, hit_stop=False, hit_target=False):
    """One ledger record. `resolved=None` leaves it PENDING."""
    outcome = {"status": "PENDING", "hit_stop": False, "hit_target": False}
    if resolved:
        outcome = {
            "status": "RESOLVED",
            f"directional_correct_{PRIMARY}m": bool(correct),
            f"pnl_{PRIMARY}m_pct": pnl,
            "directional_correct_30m": bool(correct),
            "hit_stop": hit_stop,
            "hit_target": hit_target,
        }
    return {
        "signal_id": f"SIG-{symbol}-{id(outcome)}",
        "symbol": symbol,
        "session_date": date,
        "signal_snapshot": {"regime": regime, "session_phase": "MORNING_SESSION"},
        "outcome": outcome,
    }


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------
def test_no_signals_for_the_date():
    out = PerformanceAnalyzer.session_review([], "2026-09-11")
    assert out["session_date"] == "2026-09-11"
    assert out["measurement_state"] == "NO_SIGNALS"
    assert out["total_signals"] == 0
    assert out["total_resolved"] == 0
    assert out["outcomes"] is None
    assert out["by_regime"] == {}
    assert out["by_cluster"] == {}
    assert out["best_regime"] is None


def test_filters_to_the_requested_session_date():
    signals = [_sig(date="2026-09-11"), _sig(date="2026-09-10"), _sig(date="2026-09-10")]
    out = PerformanceAnalyzer.session_review(signals, "2026-09-10")
    assert out["total_signals"] == 2


def test_signals_but_none_resolved_is_not_a_zero_win_rate():
    """The honesty requirement: N emitted, none measured — no 0.0 anywhere."""
    out = PerformanceAnalyzer.session_review([_sig(), _sig(symbol="BHEL")], "2026-09-11")
    assert out["measurement_state"] == "NONE_RESOLVED"
    assert out["total_signals"] == 2
    assert out["total_resolved"] == 0
    assert out["outcomes"] is None          # not {"win_rate_primary": 0.0}
    assert out["by_regime"] == {}
    assert out["by_cluster"] == {}
    assert out["best_regime"] is None and out["worst_regime"] is None
    assert out["best_cluster"] is None and out["worst_cluster"] is None
    assert out["primary_horizon_min"] == PRIMARY


def test_resolved_signals_produce_metrics_and_best_worst():
    signals = [
        # TREND_EXPANSION / PSU_METALS_INFRA: both correct
        _sig("SAIL", regime="TREND_EXPANSION", resolved=True, correct=True, pnl=1.5),
        _sig("NMDC", regime="TREND_EXPANSION", resolved=True, correct=True, pnl=1.0),
        # CHOP / IT: both wrong
        _sig("INFY", regime="CHOP", resolved=True, correct=False, pnl=-1.0, hit_stop=True),
        _sig("OFSS", regime="CHOP", resolved=True, correct=False, pnl=-0.5, hit_stop=True),
    ]
    out = PerformanceAnalyzer.session_review(signals, "2026-09-11")

    assert out["measurement_state"] == "MEASURED"
    assert out["total_signals"] == 4
    assert out["total_resolved"] == 4
    assert out["outcomes"]["win_rate_primary"] == 50.0
    assert out["outcomes"]["primary_horizon_min"] == PRIMARY

    assert set(out["by_regime"]) == {"TREND_EXPANSION", "CHOP"}
    assert out["best_regime"]["key"] == "TREND_EXPANSION"
    assert out["best_regime"]["win_rate"] == 100.0
    assert out["worst_regime"]["key"] == "CHOP"
    assert out["worst_regime"]["win_rate"] == 0.0


def test_cluster_grouping_uses_the_shared_cluster_map():
    """SAIL/NMDC are both PSU_METALS_INFRA in config/clusters.yaml."""
    signals = [
        _sig("SAIL", resolved=True, correct=True, pnl=1.0),
        _sig("NMDC", resolved=True, correct=True, pnl=1.0),
        _sig("INFY", resolved=True, correct=False, pnl=-1.0),
    ]
    out = PerformanceAnalyzer.session_review(signals, "2026-09-11")
    assert "PSU_METALS_INFRA" in out["by_cluster"]
    assert out["by_cluster"]["PSU_METALS_INFRA"]["total_resolved"] == 2
    assert out["best_cluster"]["key"] == "PSU_METALS_INFRA"
    assert out["worst_cluster"]["key"] == "IT"


def test_symbol_suffixes_are_normalised_before_cluster_lookup():
    signals = [_sig("SAIL-EQ", resolved=True, correct=True, pnl=1.0)]
    out = PerformanceAnalyzer.session_review(signals, "2026-09-11")
    assert "PSU_METALS_INFRA" in out["by_cluster"]


def test_pending_signals_are_counted_but_not_measured():
    signals = [
        _sig("SAIL", resolved=True, correct=True, pnl=1.0),
        _sig("BHEL"),   # pending
    ]
    out = PerformanceAnalyzer.session_review(signals, "2026-09-11")
    assert out["total_signals"] == 2
    assert out["total_resolved"] == 1
    assert out["measurement_state"] == "MEASURED"


# --------------------------------------------------------------------------
# Endpoint
# --------------------------------------------------------------------------
def test_endpoint_defaults_to_today_in_ist(monkeypatch):
    from zoneinfo import ZoneInfo
    today = datetime.datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")
    monkeypatch.setattr("signal_ledger.SignalLedger.load_all_signals",
                        staticmethod(lambda *a, **k: []))
    res = client.get("/api/session/review")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "success"
    assert body["data"]["session_date"] == today


def test_endpoint_reports_config_version_and_staleness(monkeypatch):
    monkeypatch.setattr("signal_ledger.SignalLedger.load_all_signals",
                        staticmethod(lambda *a, **k: [_sig(date="2026-09-11")]))
    res = client.get("/api/session/review?date=2026-09-11")
    d = res.json()["data"]
    assert d["measurement_state"] == "NONE_RESOLVED"
    assert isinstance(d["config_version"], int)
    assert "staleness" in d and "incidents" in d["staleness"]


def test_legacy_horizon_signals_are_excluded_not_scored_as_losses():
    """A signal RESOLVED at a retired horizon has no primary-horizon outcome.

    Counting it as a loss is how 84 old ledger entries produced a real-looking
    0% win rate at the 90m horizon. It is excluded and counted instead.
    """
    legacy = _sig("SAIL", resolved=True, correct=True, pnl=1.0)
    legacy["outcome"] = {"status": "RESOLVED", "directional_correct_30m": True,
                         "pnl_30m_pct": 1.0, "hit_stop": False, "hit_target": True}

    out = PerformanceAnalyzer.session_review([legacy], "2026-09-11")
    assert out["measurement_state"] == "NONE_RESOLVED"
    assert out["total_signals"] == 1
    assert out["total_resolved"] == 0
    assert out["legacy_excluded"] == 1
    assert out["outcomes"] is None


def test_legacy_signals_do_not_drag_down_a_measured_session():
    legacy = _sig("BHEL")
    legacy["outcome"] = {"status": "RESOLVED", "directional_correct_30m": False,
                         "pnl_30m_pct": -1.0, "hit_stop": True, "hit_target": False}
    signals = [_sig("SAIL", resolved=True, correct=True, pnl=1.0), legacy]

    out = PerformanceAnalyzer.session_review(signals, "2026-09-11")
    assert out["measurement_state"] == "MEASURED"
    assert out["total_resolved"] == 1
    assert out["legacy_excluded"] == 1
    assert out["outcomes"]["win_rate_primary"] == 100.0
