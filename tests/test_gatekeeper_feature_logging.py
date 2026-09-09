"""Task 1.4: the gatekeeper loop must log a feature vector for every symbol on
every tick, including rejections — the counterfactual is the point.

The loop itself is an infinite `while True: ... await asyncio.sleep(10)` that
mutates class-level dicts and isn't reasonably driven in a unit test, so the
decision-classification and numeric-flattening logic it uses are extracted
into pure, directly-testable helpers instead. The plan's own verification
step for this task ("run one session, inspect data/features/*.parquet") is a
live-session check and is exercised manually, not here.
"""
from reasoning_engine import _classify_decision, _numeric_features


def test_setup_rejected_wins_regardless_of_gatekeeper_state():
    math_setup = {"setup_rejected": True, "rejection_reason": "NEUTRAL_CONVICTION"}
    assert _classify_decision(math_setup, {"llm_authorized": True}) == \
        "REJECTED_NEUTRAL_CONVICTION"


def test_authorized_setup_is_proposed():
    math_setup = {"setup_rejected": False}
    assert _classify_decision(math_setup, {"llm_authorized": True}) == "PROPOSED"


def test_unauthorized_setup_is_gated_with_reason():
    math_setup = {"setup_rejected": False}
    gk = {"llm_authorized": False, "math_rejection": "STALE_DATA_20s"}
    assert _classify_decision(math_setup, gk) == "GATED_STALE_DATA_20s"


def test_numeric_features_excludes_strings_and_bools():
    payload = {"ltp": 100.5, "obi": 0.42, "symbol": "SAIL",
              "is_live": True, "vol_z": -1.2, "nested": {"a": 1}}
    out = _numeric_features(payload)
    assert out == {"ltp": 100.5, "obi": 0.42, "vol_z": -1.2}
