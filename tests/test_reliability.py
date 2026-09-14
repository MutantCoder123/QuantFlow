"""Reliability view (Task 4.4).

A reliability curve plots what the score predicted against what actually
happened. While the system is uncalibrated there is no predicted probability
yet, so the honest x-axis is |composite| itself -- which is precisely the
diagnostic Task 4.3 needs before fitting anything: if the realised win rate
is flat across composite deciles, the score has no discriminative power and
no calibration will rescue it. The view must make that obvious, not hide it.
"""
import pytest

from core.calibration import reliability_buckets


def _sig(composite, correct, minute=90, status="RESOLVED"):
    return {
        "signal_snapshot": {"composite_score": composite},
        "outcome": {"status": status, f"directional_correct_{minute}m": correct},
    }


def test_unresolved_signals_are_excluded():
    rows = [_sig(0.5, True, status="PENDING") for _ in range(20)]
    out = reliability_buckets(rows, primary_minute=90)
    assert out["n_resolved"] == 0
    assert all(b["n"] == 0 for b in out["buckets"])


def test_buckets_report_realised_win_rate_and_count():
    # 12 signals in the 0.3-0.4 decile, 9 of them winners -> 75%
    rows = [_sig(0.35, i < 9) for i in range(12)]
    out = reliability_buckets(rows, primary_minute=90, min_n=10)
    bucket = next(b for b in out["buckets"] if b["lo"] == pytest.approx(0.3))
    assert bucket["n"] == 12
    assert bucket["win_rate"] == pytest.approx(75.0)
    assert bucket["suppressed"] is False


def test_thin_buckets_are_suppressed_but_still_counted():
    """n < 10 must not be plotted as if it meant something -- but the count
    is still shown so the operator sees the gap."""
    rows = [_sig(0.55, True) for _ in range(4)]
    out = reliability_buckets(rows, primary_minute=90, min_n=10)
    bucket = next(b for b in out["buckets"] if b["lo"] == pytest.approx(0.5))
    assert bucket["n"] == 4
    assert bucket["suppressed"] is True
    assert bucket["win_rate"] is None


def test_a_flat_curve_is_flagged_as_no_discriminative_power():
    """Same win rate in every populated bucket = the score predicts nothing."""
    rows = ([_sig(0.15, i < 5) for i in range(10)] +
            [_sig(0.45, i < 5) for i in range(10)] +
            [_sig(0.75, i < 5) for i in range(10)])
    out = reliability_buckets(rows, primary_minute=90, min_n=10)
    assert out["populated_buckets"] == 3
    assert out["win_rate_spread"] == pytest.approx(0.0)
    assert out["has_discriminative_power"] is False


def test_a_sloped_curve_shows_discriminative_power():
    rows = ([_sig(0.15, i < 2) for i in range(10)] +      # 20%
            [_sig(0.45, i < 5) for i in range(10)] +      # 50%
            [_sig(0.75, i < 9) for i in range(10)])       # 90%
    out = reliability_buckets(rows, primary_minute=90, min_n=10)
    assert out["win_rate_spread"] == pytest.approx(70.0)
    assert out["has_discriminative_power"] is True


def test_legacy_horizon_signals_are_counted_not_silently_dropped():
    """Pre-Task-3.4 records were graded at 30m/60m and carry no 90m outcome.
    Folding them in would mix horizons; dropping them silently would show
    'no data' when 84 resolved signals exist. They are counted separately."""
    rows = [{"signal_snapshot": {"composite_score": 0.4},
             "outcome": {"status": "RESOLVED", "directional_correct_60m": True}}
            for _ in range(84)]
    out = reliability_buckets(rows, primary_minute=90)
    assert out["n_resolved"] == 0
    assert out["n_legacy_excluded"] == 84


def test_buckets_span_the_whole_composite_range():
    out = reliability_buckets([], primary_minute=90)
    assert len(out["buckets"]) == 10
    assert out["buckets"][0]["lo"] == pytest.approx(0.0)
    assert out["buckets"][-1]["hi"] == pytest.approx(1.0)
