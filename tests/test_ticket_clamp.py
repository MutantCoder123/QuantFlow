"""LLM-returned prices must never reach the UI unvalidated.

The prompt states adjustment bounds ("only adjust risk_parameters within these
bounds") but nothing enforced them; a hallucinated stop reached the UI as an
actionable price (A-10).
"""
import pytest
from reasoning_engine import clamp_risk_parameters

GEO = {"calculated_entry": 100.0, "padded_stop": 98.0, "calculated_target": 105.0}


def test_entry_is_clamped_to_plus_minus_0_3_pct():
    out, err = clamp_risk_parameters({"final_entry": 120.0, "final_stop": 98.0,
                                      "final_target": 105.0}, GEO, atr15=1.0, bias="LONG")
    assert err is None
    assert out["final_entry"] == pytest.approx(100.3)


def test_stop_is_clamped_to_the_atr_band():
    out, _ = clamp_risk_parameters({"final_entry": 100.0, "final_stop": 50.0,
                                    "final_target": 105.0}, GEO, atr15=1.0, bias="LONG")
    assert out["final_stop"] == pytest.approx(97.0)      # padded_stop - 1.0*ATR


def test_inverted_geometry_is_rejected_and_falls_back():
    out, err = clamp_risk_parameters({"final_entry": 100.0, "final_stop": 106.0,
                                      "final_target": 105.0}, GEO, atr15=1.0, bias="LONG")
    assert err == "LLM_GEOMETRY_REJECTED"
    assert out["final_stop"] == 98.0                      # deterministic fallback


def test_missing_values_fall_back_to_math_geometry():
    out, err = clamp_risk_parameters({}, GEO, atr15=1.0, bias="LONG")
    assert err is None
    assert (out["final_entry"], out["final_stop"], out["final_target"]) == (100.0, 98.0, 105.0)
