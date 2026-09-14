"""One shared answer to "does this signal carry an outcome at the horizon in
force?" (Task 5.6 fix-round 1).

The invariant is NOT "the record has a directional_correct_90m key". A signal
whose stop or target was hit at the 30m checkpoint is CLOSED -- the position is
gone, there is nothing left to measure at 90m, and `_resolve_one` deliberately
breaks out of the checkpoint loop. That record is measured, not legacy.

The real invariant: a record is excluded only when it cannot be attributed to
the horizon config in force.
"""
import pytest

from core.outcome_schema import (LEGACY, MEASURED, PENDING, classify,
                                 normalise, primary_outcome)

MINS = [30, 90]


def _sig(outcome, **kw):
    s = {"symbol": "SAIL", "session_date": "2026-09-11",
         "signal_snapshot": {"regime": "TREND_EXPANSION"}, "outcome": outcome}
    s.update(kw)
    return s


# --------------------------------------------------------------------------
# The three classes
# --------------------------------------------------------------------------
def test_pending_is_not_measurable():
    s = _sig({"status": "PENDING", "hit_stop": False, "hit_target": False})
    assert classify(s, MINS) == PENDING
    assert primary_outcome(s, MINS) is None


def test_full_horizon_resolution_is_measured():
    s = _sig({"status": "RESOLVED", "directional_correct_90m": True,
              "pnl_90m_pct": 1.4, "hit_stop": False, "hit_target": False})
    assert classify(s, MINS) == MEASURED
    po = primary_outcome(s, MINS)
    assert po["directional_correct"] is True
    assert po["pnl_pct"] == 1.4
    assert po["early"] is False
    assert po["decided_at_min"] == 90


# --- the Critical: early resolution is a primary-horizon outcome ------------
def test_early_target_hit_at_30m_is_measured_not_legacy():
    """The bug this fix-round exists for: a TARGET HIT was reported as
    'not measured yet, graded at a retired horizon'."""
    s = _sig({"status": "RESOLVED_EARLY", "directional_correct_30m": True,
              "pnl_30m_pct": 2.1, "hit_stop": False, "hit_target": True})
    assert classify(s, MINS) == MEASURED
    po = primary_outcome(s, MINS)
    assert po["directional_correct"] is True
    assert po["pnl_pct"] == 2.1
    assert po["early"] is True
    assert po["decided_at_min"] == 30


def test_early_stop_hit_at_30m_is_measured_as_a_loss():
    s = _sig({"status": "RESOLVED_EARLY", "directional_correct_30m": False,
              "pnl_30m_pct": -1.3, "hit_stop": True, "hit_target": False})
    assert classify(s, MINS) == MEASURED
    po = primary_outcome(s, MINS)
    assert po["directional_correct"] is False
    assert po["pnl_pct"] == -1.3


def test_early_resolution_keeps_the_cost_floor_verdict():
    """A target so close it does not clear round-trip costs is NOT a win.

    directional_correct comes from label_outcome (gross > cost_pct); deriving
    it from hit_target alone would quietly drop the cost floor.
    """
    s = _sig({"status": "RESOLVED_EARLY", "directional_correct_30m": False,
              "pnl_30m_pct": 0.02, "hit_stop": False, "hit_target": True})
    assert primary_outcome(s, MINS)["directional_correct"] is False


def test_early_resolution_at_the_primary_checkpoint_is_not_flagged_early():
    s = _sig({"status": "RESOLVED_EARLY", "directional_correct_90m": True,
              "pnl_90m_pct": 3.0, "hit_stop": False, "hit_target": True})
    po = primary_outcome(s, MINS)
    assert po["decided_at_min"] == 90
    assert po["directional_correct"] is True


# --- genuinely legacy -------------------------------------------------------
def test_retired_checkpoint_key_is_legacy():
    """60m is not in the horizon config any more; a 60m outcome is not a 90m one."""
    s = _sig({"status": "RESOLVED", "directional_correct_60m": True,
              "pnl_60m_pct": 1.0})
    assert classify(s, MINS) == LEGACY
    assert primary_outcome(s, MINS) is None


def test_pre_bar_accurate_resolver_records_are_legacy():
    """`ltp_at_*` was written only by the LTP-sampling resolver replaced in
    the bar-accurate rewrite (C-3). Its presence dates the record."""
    s = _sig({"status": "RESOLVED_EARLY", "directional_correct_30m": True,
              "pnl_30m_pct": 1.0, "ltp_at_30m": 101.2,
              "hit_stop": False, "hit_target": True})
    assert classify(s, MINS) == LEGACY


def test_early_record_with_no_checkpoint_measurement_is_legacy():
    """Decided early but carrying no per-checkpoint outcome at all: there is
    no horizon to attribute it to and no magnitude to report."""
    s = _sig({"status": "RESOLVED_EARLY", "hit_stop": True, "hit_target": False})
    assert classify(s, MINS) == LEGACY


# --- the stamp: the durable answer for records written from now on ---------
def test_a_stamped_record_is_trusted_over_the_heuristics():
    s = _sig({"status": "RESOLVED_EARLY", "directional_correct_30m": True,
              "pnl_30m_pct": 1.0, "hit_target": True},
             horizon={"measure_at_minutes": [30, 90]})
    assert classify(s, MINS) == MEASURED


def test_a_record_stamped_with_a_different_horizon_is_legacy():
    s = _sig({"status": "RESOLVED", "directional_correct_60m": True,
              "pnl_60m_pct": 1.0},
             horizon={"measure_at_minutes": [30, 60]})
    assert classify(s, MINS) == LEGACY


# --- normalise --------------------------------------------------------------
def test_normalise_projects_an_early_outcome_onto_the_primary_keys():
    """So _compute_metrics -- shared, deliberately untouched -- reads it."""
    s = _sig({"status": "RESOLVED_EARLY", "directional_correct_30m": True,
              "pnl_30m_pct": 2.1, "hit_stop": False, "hit_target": True})
    n = normalise(s, MINS)
    assert n["outcome"]["directional_correct_90m"] is True
    assert n["outcome"]["pnl_90m_pct"] == 2.1
    assert n["outcome"]["hit_target"] is True


def test_normalise_does_not_mutate_the_input_record():
    outcome = {"status": "RESOLVED_EARLY", "directional_correct_30m": True,
               "pnl_30m_pct": 2.1, "hit_target": True}
    s = _sig(outcome)
    normalise(s, MINS)
    assert "directional_correct_90m" not in outcome
    assert "pnl_90m_pct" not in outcome


def test_normalise_returns_none_for_legacy_and_pending():
    assert normalise(_sig({"status": "PENDING"}), MINS) is None
    assert normalise(_sig({"status": "RESOLVED",
                           "directional_correct_60m": True}), MINS) is None


# --------------------------------------------------------------------------
# Retirement is keyed off the PRIMARY horizon, not the full checkpoint list
# (fix-round 2). Adding or dropping a diagnostic checkpoint must not retire
# history measured at a primary horizon that never moved -- that would be the
# same false "graded at a retired horizon" claim the early-close fix removed,
# just triggered by a config edit instead.
# --------------------------------------------------------------------------
def test_adding_a_diagnostic_checkpoint_does_not_retire_measured_history():
    """[30, 90] -> [15, 30, 90]: the primary horizon is still 90."""
    s = _sig({"status": "RESOLVED", "directional_correct_90m": True,
              "pnl_90m_pct": 1.4, "hit_stop": False, "hit_target": False},
             horizon={"measure_at_minutes": [30, 90]})
    assert classify(s, [15, 30, 90]) == MEASURED
    assert primary_outcome(s, [15, 30, 90])["directional_correct"] is True


def test_dropping_a_diagnostic_checkpoint_does_not_retire_measured_history():
    """[30, 90] -> [90]: still a 90m primary horizon."""
    s = _sig({"status": "RESOLVED", "directional_correct_90m": True,
              "pnl_90m_pct": 1.4},
             horizon={"measure_at_minutes": [30, 90]})
    assert classify(s, [90]) == MEASURED


def test_an_early_close_survives_a_diagnostic_checkpoint_being_added():
    s = _sig({"status": "RESOLVED_EARLY", "directional_correct_30m": True,
              "pnl_30m_pct": 2.1, "hit_target": True},
             horizon={"measure_at_minutes": [30, 90]})
    assert classify(s, [15, 30, 90]) == MEASURED


def test_moving_the_primary_horizon_does_retire_stamped_history():
    """[30, 90] -> [30, 120] is a real horizon change: a 90m outcome is not a
    120m one, and mixing them is what the exclusion exists to prevent."""
    s = _sig({"status": "RESOLVED", "directional_correct_90m": True,
              "pnl_90m_pct": 1.4},
             horizon={"measure_at_minutes": [30, 90]})
    assert classify(s, [30, 120]) == LEGACY


def test_unstamped_record_measured_at_the_primary_horizon_survives_extra_keys():
    """An older unstamped record carrying a valid primary-horizon outcome stays
    measured even if it also carries a checkpoint the config has since dropped."""
    s = _sig({"status": "RESOLVED",
              "directional_correct_60m": True, "pnl_60m_pct": 0.9,
              "directional_correct_90m": True, "pnl_90m_pct": 1.4})
    assert classify(s, MINS) == MEASURED
    assert primary_outcome(s, MINS)["decided_at_min"] == 90


def test_unstamped_record_with_only_retired_checkpoints_is_still_legacy():
    """The guard that must survive the change: no outcome at the horizon in
    force, and a checkpoint that is no longer measured."""
    s = _sig({"status": "RESOLVED_EARLY", "directional_correct_30m": True,
              "pnl_30m_pct": 1.0, "directional_correct_60m": True,
              "pnl_60m_pct": 1.2, "hit_target": True})
    assert classify(s, MINS) == LEGACY
