"""Keep the semantic tags, but stop discarding the numbers (improved §4.7),
and stop letting a hardcoded-dead catalyst weight cap the composite (C-1).
"""
import pytest

from semantic_tagger import SemanticTagger, state_of
from conviction_scorer import ConvictionScorer


def test_semantic_block_carries_the_scalar_alongside_the_tag():
    out = SemanticTagger.translate_to_llm_payload(
        {"ltp": 100.0, "vol_z_score_5m": 3.4, "obi": 0.71, "atr_1d": 2.0})
    vr = out["1_live_microstructure"]["volume_regime"]
    assert vr["state"] == "TIME_ADJUSTED_SHOCK"
    assert vr["vol_z"] == 3.4


def test_state_of_accepts_both_shapes():
    assert state_of({"state": "X", "v": 1}) == "X"
    assert state_of("X") == "X"
    assert state_of(None) is None


def test_composite_can_reach_one_when_catalyst_is_dead():
    """w_cat multiplied a hardcoded 0.0, so |composite| could never exceed
    0.75 in TREND_EXPANSION. Renormalising over the live components only
    removes that artificial cap."""
    s = ConvictionScorer()
    w = s._normalized_weights("TREND_EXPANSION", catalyst_live=False)
    assert w["w_micro"] == pytest.approx(0.45 / 0.75)
    assert w["w_cat"] == 0.0
    assert sum(v for k, v in w.items() if k != "w_cat") == pytest.approx(1.0)


def test_normalized_weights_keep_catalyst_when_it_is_live():
    s = ConvictionScorer()
    w = s._normalized_weights("TREND_EXPANSION", catalyst_live=True)
    assert w["w_cat"] == pytest.approx(0.25)
    assert sum(w.values()) == pytest.approx(1.0)
