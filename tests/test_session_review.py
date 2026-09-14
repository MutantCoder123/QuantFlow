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


def _early(symbol="SAIL", date="2026-09-11", regime="TREND_EXPANSION",
           target=True, minute=30, correct=None):
    """A trade closed at the `minute` checkpoint by a stop or target hit.

    _resolve_one stamps RESOLVED_EARLY and breaks out of the checkpoint loop,
    so there is deliberately no key at the primary horizon.
    """
    s = _sig(symbol, date, regime)
    s["outcome"] = {
        "status": "RESOLVED_EARLY",
        f"directional_correct_{minute}m": bool(target if correct is None else correct),
        f"pnl_{minute}m_pct": 2.1 if target else -1.3,
        "hit_stop": not target,
        "hit_target": target,
    }
    return s


def _old_schema(symbol="SAIL", date="2026-09-11", correct=True):
    """A pre-C-3 record: graded at the retired 60m checkpoint, and carrying
    the `ltp_at_*` keys only the old LTP-sampling resolver ever wrote."""
    s = _sig(symbol, date)
    s["outcome"] = {
        "status": "RESOLVED",
        "directional_correct_30m": correct, "pnl_30m_pct": 1.0, "ltp_at_30m": 101.0,
        "directional_correct_60m": correct, "pnl_60m_pct": 1.0, "ltp_at_60m": 101.5,
        "hit_stop": False, "hit_target": False,
    }
    return s


# --------------------------------------------------------------------------
# The real invariant: a record is excluded only when it cannot be attributed
# to the horizon config in force -- NOT merely because it lacks a 90m key.
# An early stop/target hit lacks one too, and it is measured.
# --------------------------------------------------------------------------
def test_early_target_hit_is_measured_not_reported_as_unmeasured():
    """The trade closed at 30m with a TARGET HIT -- the most decisive positive
    outcome the system produces. It was being shown as 'not measured yet,
    graded at a retired horizon', which is simply false."""
    out = PerformanceAnalyzer.session_review([_early(target=True)], "2026-09-11")
    assert out["measurement_state"] == "MEASURED"
    assert out["total_resolved"] == 1
    assert out["legacy_excluded"] == 0
    assert out["outcomes"]["win_rate_primary"] == 100.0


def test_early_stop_hit_is_measured_as_a_loss():
    out = PerformanceAnalyzer.session_review([_early(target=False)], "2026-09-11")
    assert out["measurement_state"] == "MEASURED"
    assert out["total_resolved"] == 1
    assert out["outcomes"]["win_rate_primary"] == 0.0
    assert out["outcomes"]["stop_hit_rate"] == 100.0


def test_genuinely_old_schema_records_are_still_excluded():
    out = PerformanceAnalyzer.session_review([_old_schema()], "2026-09-11")
    assert out["measurement_state"] == "NONE_RESOLVED"
    assert out["total_signals"] == 1
    assert out["total_resolved"] == 0
    assert out["legacy_excluded"] == 1
    assert out["outcomes"] is None


def test_resolved_without_a_primary_key_is_not_read_as_an_early_close():
    """Only RESOLVED_EARLY with a stop/target hit may be measured below the
    primary horizon. A plain RESOLVED missing the 90m key was graded under an
    older schema; reading its 30m result as a 90m one would mix horizons."""
    s = _sig("SAIL")
    s["outcome"] = {"status": "RESOLVED", "directional_correct_30m": True,
                    "pnl_30m_pct": 1.0, "hit_stop": False, "hit_target": False}
    out = PerformanceAnalyzer.session_review([s], "2026-09-11")
    assert out["measurement_state"] == "NONE_RESOLVED"
    assert out["legacy_excluded"] == 1


def test_mixed_session_measures_early_closes_and_excludes_only_the_old():
    """The survivorship trap: if early closes were excluded, the win rate
    would be computed only from trades that hit NEITHER stop nor target."""
    signals = [
        _early("SAIL", target=True),                               # win, closed at 30m
        _early("NMDC", target=False),                              # loss, stopped at 30m
        _sig("INFY", resolved=True, correct=True, pnl=1.0),        # win, full 90m
        _sig("BHEL"),                                              # pending
        _old_schema("OFSS"),                                       # excluded
    ]
    out = PerformanceAnalyzer.session_review(signals, "2026-09-11")

    assert out["measurement_state"] == "MEASURED"
    assert out["total_signals"] == 5
    assert out["total_resolved"] == 3        # both early closes counted
    assert out["legacy_excluded"] == 1
    assert out["outcomes"]["win_rate_primary"] == round(2 / 3 * 100, 2)


def test_early_closes_reach_the_grouped_views():
    out = PerformanceAnalyzer.session_review(
        [_early("SAIL", target=True), _early("INFY", regime="CHOP", target=False)],
        "2026-09-11")
    assert out["by_regime"]["TREND_EXPANSION"]["total_resolved"] == 1
    assert out["best_regime"]["key"] == "TREND_EXPANSION"
    assert out["worst_regime"]["key"] == "CHOP"
    assert out["by_cluster"]["PSU_METALS_INFRA"]["total_resolved"] == 1


def test_a_horizon_stamped_record_is_attributed_by_its_stamp():
    """record_signal now stamps the horizon config, so future records need no
    dating heuristic at all."""
    s = _early("SAIL", target=True)
    s["horizon"] = {"measure_at_minutes": [30, PRIMARY]}
    out = PerformanceAnalyzer.session_review([s], "2026-09-11")
    assert out["measurement_state"] == "MEASURED"

    stale = _early("NMDC", target=True)
    stale["horizon"] = {"measure_at_minutes": [30, 60]}
    out2 = PerformanceAnalyzer.session_review([stale], "2026-09-11")
    assert out2["measurement_state"] == "NONE_RESOLVED"
    assert out2["legacy_excluded"] == 1
