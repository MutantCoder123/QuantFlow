"""compute_dashboard / compute_regime_accuracy / compute_symbol_accuracy must
not score records they cannot attribute to the horizon in force.

session_review and get_feedback_payload already partition the ledger through
core.outcome_schema. These three loaded it raw, so a record graded at a
retired checkpoint -- which carries no ``directional_correct_{primary}m`` key
-- was read by _compute_metrics as False: a LOSS. On today's ledger (84 such
records, 0 measurable) that is a confident 0% win rate built from nothing.
"""
import pytest

from performance_analyzer import PerformanceAnalyzer

PRIMARY = PerformanceAnalyzer._primary_minute()


def _base(symbol, regime):
    return {"signal_id": f"SIG-{symbol}-{regime}", "symbol": symbol,
            "session_date": "2026-09-11",
            "signal_snapshot": {"regime": regime}}


def _old_schema(symbol="SAIL", regime="TREND_EXPANSION"):
    """Graded at the retired 60m checkpoint by the pre-C-3 resolver."""
    s = _base(symbol, regime)
    s["outcome"] = {
        "status": "RESOLVED",
        "directional_correct_30m": True, "pnl_30m_pct": 1.0, "ltp_at_30m": 101.0,
        "directional_correct_60m": True, "pnl_60m_pct": 1.0, "ltp_at_60m": 101.5,
        "hit_stop": False, "hit_target": False,
    }
    return s


def _current(symbol="BHEL", regime="TREND_EXPANSION", correct=True):
    s = _base(symbol, regime)
    s["outcome"] = {
        "status": "RESOLVED",
        f"directional_correct_{PRIMARY}m": correct,
        f"pnl_{PRIMARY}m_pct": 1.2 if correct else -0.8,
        "directional_correct_30m": correct,
        "hit_stop": False, "hit_target": False,
    }
    return s


def _early_target(symbol="NMDC", regime="TREND_EXPANSION"):
    """Target hit at 30m -- closed, so this IS its primary outcome."""
    s = _base(symbol, regime)
    s["outcome"] = {"status": "RESOLVED_EARLY", "directional_correct_30m": True,
                    "pnl_30m_pct": 2.1, "hit_stop": False, "hit_target": True}
    return s


@pytest.fixture
def ledger(monkeypatch):
    def _set(records):
        monkeypatch.setattr("signal_ledger.SignalLedger.load_all_signals",
                            lambda *a, **k: [dict(r) for r in records])
    return _set


# -- the defect: legacy-only ledger must not produce a 0% win rate ----------
def test_dashboard_does_not_score_legacy_records_as_losses(ledger):
    ledger([_old_schema("SAIL"), _old_schema("NMDC")])
    out = PerformanceAnalyzer.compute_dashboard()
    assert out["overall"].get("total_resolved", 0) == 0
    assert "win_rate_primary" not in out["overall"]
    assert out["legacy_excluded"] == 2


def test_regime_accuracy_has_no_bucket_built_from_legacy_records(ledger):
    ledger([_old_schema(regime="TREND_EXPANSION")])
    assert PerformanceAnalyzer.compute_regime_accuracy() == {}


def test_symbol_accuracy_has_no_bucket_built_from_legacy_records(ledger):
    ledger([_old_schema(symbol="SAIL")])
    assert PerformanceAnalyzer.compute_symbol_accuracy() == {}


# -- the other direction: exclusion must not swallow real outcomes ----------
def test_mixed_ledger_measures_only_the_attributable(ledger):
    ledger([_old_schema("SAIL"), _current("BHEL", correct=True),
            _current("INFY", correct=False)])
    out = PerformanceAnalyzer.compute_dashboard()
    assert out["overall"]["total_resolved"] == 2
    assert out["overall"]["win_rate_primary"] == 50.0
    assert out["legacy_excluded"] == 1
    assert set(out["by_symbol"]) == {"BHEL", "INFY"}


def test_early_target_hit_counts_as_a_win_not_legacy(ledger):
    ledger([_early_target("NMDC")])
    out = PerformanceAnalyzer.compute_dashboard()
    assert out["overall"]["total_resolved"] == 1
    assert out["overall"]["win_rate_primary"] == 100.0
    assert out["legacy_excluded"] == 0
    assert PerformanceAnalyzer.compute_symbol_accuracy()["NMDC"]["win_rate_primary"] == 100.0
