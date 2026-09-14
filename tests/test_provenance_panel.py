"""Provenance panel (Task 5.2, improved §5.1): score_setup must expose the
per-category raw/normalized/weight/contribution numbers plus the exact
signal names that fired, instead of discarding them once composite is
computed. This is what lets an operator calibrate their own trust in a
setup -- and the dead catalyst term must be marked, not silently zeroed.
"""
import pytest

from conviction_scorer import ConvictionScorer

REGIME = {"current_regime": "TREND_EXPANSION", "session_phase": "MORNING_SESSION"}

FLAT = {"ltp": 100.0, "atr_15m": 0.5, "atr_5m": 0.1,
        "rolling_20d_value_area_low": 99.5,
        "rolling_20d_value_area_high": 105.0}

# Only microstructure signals fire; struct/deriv/catalyst are all inert.
MICRO_ONLY_PAYLOAD = {
    "market_regime": REGIME,
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

STRUCT_DERIV_PAYLOAD = {
    "market_regime": REGIME,
    "1_live_microstructure": {},
    "2_derivatives_matrix_52w": {
        "pcr_regime": "EXTREME_PUT_WRITING",
        "options_gravity_state": "ESCAPE_VELOCITY_ACHIEVED",
    },
    "3_local_structural_edge_20d": {
        "momentum_confluence": {"state": "STRONG_ALPHA"},
        "structural_proximity_state": {
            "state": "TEST_IMMINENT",
            "nearest_level": "rolling_20d_value_area_low",
            "approach_direction": "TESTING_FROM_ABOVE",
        },
    },
}


def test_contributions_present_with_all_four_categories():
    out = ConvictionScorer().score_setup(MICRO_ONLY_PAYLOAD, FLAT)
    assert out["setup_rejected"] is False, out.get("rejection_reason")
    assert set(out["contributions"].keys()) == {"micro", "struct", "deriv", "catalyst"}


def test_micro_contribution_matches_raw_norm_weight_and_lists_firing_signals():
    out = ConvictionScorer().score_setup(MICRO_ONLY_PAYLOAD, FLAT)
    micro = out["contributions"]["micro"]
    # flow (+3) + obi (+1) + fractal (+2) = 6 -> normalized capped at 1.0
    assert micro["raw"] == pytest.approx(6.0)
    assert micro["normalized"] == pytest.approx(1.0)
    assert micro["contribution"] == pytest.approx(
        micro["normalized"] * micro["weight"], abs=1e-3)
    assert micro["firing_signals"] == [
        "flow_divergence MOMENTUM_CONFIRMED_BULLISH",
        "order_book_imbalance EXTREME_BID_DOMINANCE",
        "fractal_alignment STRONG_FRACTAL_BULL",
    ]


def test_inert_categories_report_zero_with_no_firing_signals():
    out = ConvictionScorer().score_setup(MICRO_ONLY_PAYLOAD, FLAT)
    for key in ("struct", "deriv"):
        cat = out["contributions"][key]
        assert cat["raw"] == pytest.approx(0.0)
        assert cat["contribution"] == pytest.approx(0.0)
        assert cat["firing_signals"] == []


def test_catalyst_is_marked_dead_not_a_fabricated_zero_score():
    out = ConvictionScorer().score_setup(MICRO_ONLY_PAYLOAD, FLAT)
    cat = out["contributions"]["catalyst"]
    assert cat["raw"] is None
    assert cat["dead"] is True
    assert cat["marker"] == "⚠ no catalyst input (see C-1)"
    assert cat["contribution"] == pytest.approx(0.0)


def test_struct_and_deriv_firing_signals_are_named_precisely():
    out = ConvictionScorer().score_setup(STRUCT_DERIV_PAYLOAD, FLAT)
    assert out["setup_rejected"] is False, out.get("rejection_reason")
    struct = out["contributions"]["struct"]
    deriv = out["contributions"]["deriv"]
    assert struct["firing_signals"] == [
        "momentum_confluence STRONG_ALPHA",
        "structural_proximity TEST_IMMINENT at rolling_20d_value_area_low (testing from above)",
    ]
    assert deriv["firing_signals"] == [
        "pcr_regime EXTREME_PUT_WRITING",
        "options_gravity ESCAPE_VELOCITY_ACHIEVED",
    ]
    # momentum (+2) + proximity (+2) = 4 -> normalized capped at 1.0
    assert struct["raw"] == pytest.approx(4.0)
    assert struct["normalized"] == pytest.approx(1.0)
    # pcr (+2) + gravity (+1) = 3 -> normalized exactly 1.0
    assert deriv["raw"] == pytest.approx(3.0)
    assert deriv["normalized"] == pytest.approx(1.0)


def test_rejected_setup_still_returns_a_contributions_key():
    """NEUTRAL_CONVICTION rejects after category scores are computed but
    before geometry is built -- the operator should still see why."""
    out = ConvictionScorer().score_setup(
        {"market_regime": REGIME, "1_live_microstructure": {},
         "2_derivatives_matrix_52w": {}, "3_local_structural_edge_20d": {}},
        {"ltp": 100.0})
    assert out["setup_rejected"] is True
    assert out["rejection_reason"] == "NEUTRAL_CONVICTION"
    assert "contributions" in out  # must not crash; may be None/empty


def test_market_closed_rejection_does_not_crash_without_contributions():
    """The MARKET_CLOSED guard fires before any category is scored -- there
    is genuinely nothing to report yet, so contributions must be tolerated
    as missing rather than fabricated."""
    out = ConvictionScorer().score_setup(
        {"market_regime": {"current_regime": "MARKET_CLOSED"}}, {"ltp": 100.0})
    assert out["setup_rejected"] is True
    assert "contributions" in out
