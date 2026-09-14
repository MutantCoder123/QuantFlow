"""Attention ranking (improved §4.6): the gate becomes a ranked attention
queue instead of a flat pass/fail. `attention_rank(sp)` orders proposals by
`ev_r x freshness` -- a stale proposal ranks below a fresh one with equal EV,
and a rejected setup always sinks to the bottom (-inf) so it never surfaces
in a top-N escalation or a top-of-grid display.

No calibrated "confidence" field exists post-Task-4.2 (the invented sigmoid
probability was deleted as never-fitted-to-outcomes) -- ev_r uses the
measured `statistical_edge` when present, else the reward:risk margin above
breakeven (the same fallback the gate itself uses in conviction_scorer.py).
"""
from reasoning_engine import attention_rank


def _sp(*, rejected=False, statistical_edge=None, reward_risk=0.0, data_age_s=0.0):
    return {
        "math_setup": {
            "setup_rejected": rejected,
            "expectancy_matrix": {
                "statistical_edge": statistical_edge,
                "reward_risk": reward_risk,
            },
        },
        "data_age_s": data_age_s,
    }


def test_stale_proposal_ranks_below_fresh_with_equal_ev():
    fresh = _sp(statistical_edge=0.3, data_age_s=0.0)
    stale = _sp(statistical_edge=0.3, data_age_s=120.0)
    assert attention_rank(fresh) > attention_rank(stale)


def test_rejected_setup_ranks_at_negative_infinity():
    rejected = _sp(rejected=True, statistical_edge=0.5, data_age_s=0.0)
    assert attention_rank(rejected) == float("-inf")


def test_missing_math_setup_is_treated_as_rejected():
    assert attention_rank({}) == float("-inf")


def test_higher_ev_ranks_above_lower_ev_at_equal_freshness():
    strong = _sp(statistical_edge=0.5, data_age_s=10.0)
    weak = _sp(statistical_edge=0.1, data_age_s=10.0)
    assert attention_rank(strong) > attention_rank(weak)


def test_falls_back_to_reward_risk_margin_when_no_statistical_edge():
    # No statistical_edge measured yet -- ev_r proxy is reward_risk - 1.0,
    # matching conviction_scorer.py's own breakeven-margin fallback.
    sp = _sp(statistical_edge=None, reward_risk=2.0, data_age_s=0.0)
    assert attention_rank(sp) == 1.0  # (2.0 - 1.0) * freshness(age=0) == 1.0


def test_freshness_decays_with_age_per_spec():
    # freshness = 1 / (1 + age/60): 1.0 at age 0, 0.5 at 60s.
    sp0 = _sp(statistical_edge=1.0, data_age_s=0.0)
    sp60 = _sp(statistical_edge=1.0, data_age_s=60.0)
    assert attention_rank(sp0) == 1.0
    assert attention_rank(sp60) == 0.5
