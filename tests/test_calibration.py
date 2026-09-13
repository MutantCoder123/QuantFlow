"""Report None until calibrated (improved §4.2, fixes C-1).

implied_probability was a fabricated statistic: 0.50 + 0.85*(sigmoid(4.5*
|composite|) - 0.50). It looked like a probability, was rendered as one, and
was fed to the LLM as "ground truth" -- but nothing had ever been measured.
An uncalibrated system must say so, not invent 64%.
"""
import json

import pytest

from core.calibration import (Calibration, implied_probability,
                              load_calibration, calibration_status, MIN_N,
                              reliability_buckets)
from conviction_scorer import ConvictionScorer

SEMANTIC_FIXTURE = {
    "market_regime": {"current_regime": "TREND_EXPANSION",
                      "session_phase": "MORNING_SESSION"},
    "1_live_microstructure": {
        "flow_divergence_state": "MOMENTUM_CONFIRMED_BULLISH",
        "order_book_imbalance_state": "EXTREME_BID_DOMINANCE",
        "fractal_alignment": "STRONG_FRACTAL_BULL",
        "volume_regime": "NORMAL_DRIFT",
        "session_cost_basis_state": "AT_EQUILIBRIUM",
        "kinetic_divergence": "MOMENTUM_CONFIRMED",
        "elasticity_risk": "EQUILIBRIUM",
        "volatility_state": "NORMAL_RANGING",
    },
    "2_derivatives_matrix_52w": {},
    "3_local_structural_edge_20d": {},
}

# A tight floor and a distant ceiling -> a genuinely favourable reward:risk.
FLAT_FIXTURE = {"ltp": 100.0, "atr_15m": 0.5, "atr_5m": 0.1,
                "rolling_20d_value_area_low": 99.5,
                "rolling_20d_value_area_high": 105.0}


# --------------------------------------------------------------------------
# Calibration object
# --------------------------------------------------------------------------
def test_implied_probability_is_none_without_calibration():
    assert implied_probability(0.34, "TREND_EXPANSION", cal=None) is None


def test_load_calibration_returns_none_when_file_absent(tmp_path):
    assert load_calibration(tmp_path / "nope.json") is None


def test_calibration_below_min_n_is_not_usable_and_predicts_none():
    cal = Calibration(b0=-0.2, b1=2.0, n_resolved=MIN_N - 1, fitted_at="x")
    assert cal.is_usable is False
    assert cal.predict(0.34) is None
    assert implied_probability(0.34, "TREND_EXPANSION", cal=cal) is None


def test_usable_calibration_predicts_a_probability():
    cal = Calibration(b0=0.0, b1=2.0, n_resolved=MIN_N, fitted_at="x")
    p = cal.predict(0.0)
    assert p == pytest.approx(0.5)
    assert 0.5 < cal.predict(1.0) < 1.0


def test_load_calibration_reads_a_fitted_file(tmp_path):
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps({"b0": 0.1, "b1": 1.5, "n_resolved": 250,
                                "fitted_at": "2026-09-12"}), encoding="utf-8")
    cal = load_calibration(path)
    assert cal is not None and cal.n_resolved == 250 and cal.is_usable


def test_status_string_names_the_shortfall():
    assert "unmeasured" in calibration_status(None)
    assert str(MIN_N) in calibration_status(None)


# --------------------------------------------------------------------------
# Scorer falls back to reward:risk while uncalibrated
# --------------------------------------------------------------------------
def test_stat_edge_gate_falls_back_to_rr_when_uncalibrated():
    """With no probability, admit on reward:risk alone rather than
    inventing 64%."""
    out = ConvictionScorer().score_setup(SEMANTIC_FIXTURE, FLAT_FIXTURE)
    assert out["setup_rejected"] is False, out.get("rejection_reason")
    em = out["expectancy_matrix"]
    assert em["implied_probability"] is None
    assert em["statistical_edge"] is None
    assert em["reward_risk"] >= 1.5


def test_breakeven_is_still_reported_because_geometry_is_known():
    """Breakeven comes from the geometry, not from a guessed probability --
    it stays measurable and useful."""
    em = ConvictionScorer().score_setup(SEMANTIC_FIXTURE, FLAT_FIXTURE)["expectancy_matrix"]
    assert 0.0 < em["breakeven_probability"] < 1.0


# --------------------------------------------------------------------------
# reliability_buckets: the reported label must match the curve actually drawn
# --------------------------------------------------------------------------
def test_reliability_buckets_label_and_curve_agree_when_primary_minute_disagrees_with_policy():
    """A `primary_minute` that disagrees with the policy's own primary horizon
    (default policy is [30, 90]) must not silently borrow the policy's
    checkpoint list -- a 90m-only record must not be counted, and mislabelled,
    as a 60m outcome. Reproduces the bug: `primary_minute=60` against a
    [30, 90] policy previously counted the 90m outcome while claiming a 60m
    curve. The fix must make one of two things true: either the 90m record is
    excluded (not attributable to a 60m checkpoint) while the label honestly
    says 60m, or the record IS read at 90m and the label says 90m. What must
    never happen is a 90m outcome being counted under a 60m label.
    """
    signals = [{"outcome": {"status": "RESOLVED", "directional_correct_90m": True,
                            "pnl_90m_pct": 1.0},
                "signal_snapshot": {"composite_score": 0.85}}]
    out = reliability_buckets(signals, primary_minute=60)

    if out["primary_minute"] == 60:
        # Labelled 60m -> the 90m-only record must NOT have been counted as
        # resolved at that horizon (it has no 60m checkpoint at all).
        assert out["n_resolved"] == 0
        assert out["n_legacy_excluded"] == 1
    else:
        # Otherwise the curve was genuinely drawn at 90m, so it must say so.
        assert out["primary_minute"] == 90
        assert out["n_resolved"] == 1


def test_poor_reward_risk_is_rejected_while_uncalibrated():
    flat = dict(FLAT_FIXTURE)
    # Move the floor far away -> wide stop, poor reward:risk.
    flat["rolling_20d_value_area_low"] = 90.0
    flat["rolling_20d_value_area_high"] = 100.6
    out = ConvictionScorer().score_setup(SEMANTIC_FIXTURE, flat)
    assert out["setup_rejected"] is True
    assert out["rejection_reason"] == "INSUFFICIENT_REWARD_RISK"
