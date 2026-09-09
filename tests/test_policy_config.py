import pytest
from core.policy_config import load_policy, PolicyConfig


def test_default_config_loads_and_is_versioned():
    cfg = load_policy()
    assert isinstance(cfg, PolicyConfig)
    assert cfg.version >= 1


def test_known_thresholds_match_current_code_constants():
    """v1 must reproduce today's behaviour exactly — this is a refactor, not a
    retune. Any change of behaviour belongs in v2 after replay evidence."""
    cfg = load_policy()
    assert cfg.semantic["vol_z_shock"] == 2.5           # semantic_tagger.py:82
    assert cfg.semantic["obi_extreme"] == 0.60          # semantic_tagger.py:92
    assert cfg.conviction["bias_threshold"] == 0.15     # conviction_scorer.py:151
    assert cfg.gates["min_stat_edge"] == 0.05           # conviction_scorer.py:274
    assert cfg.gates["regime_dampening"]["RANGE_BOUND_CHOP"] == 0.6


def test_missing_required_key_raises():
    with pytest.raises(ValueError, match="missing required"):
        PolicyConfig.from_dict({"version": 1, "semantic": {}})


def test_config_is_immutable():
    cfg = load_policy()
    with pytest.raises(Exception):
        cfg.version = 99
