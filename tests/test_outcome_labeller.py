import pandas as pd
from journal.outcome_labeller import label_outcome


def _bars():
    return pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-09-09 10:00", "2026-09-09 10:05",
                                      "2026-09-09 10:10", "2026-09-09 10:15"]),
        "open":  [100.0, 100.5, 100.2, 101.0],
        "high":  [100.8, 101.0,  99.6, 103.5],
        "low":   [ 99.9, 100.1,  98.9, 100.8],
        "close": [100.5, 100.2, 101.0, 103.0],
    })


TS = int(pd.Timestamp("2026-09-09 10:00").timestamp())


def test_intrabar_stop_touch_is_detected():
    """A 60s LTP sample would miss the 98.9 low; the bar's low must not."""
    out = label_outcome(_bars(), TS, entry=100.0, stop=99.0, target=105.0,
                        bias="LONG", horizon_min=60, cost_pct=0.06)
    assert out["hit_stop"] is True
    assert out["outcome"] == "STOP"


def test_target_hit_when_stop_untouched():
    out = label_outcome(_bars(), TS, entry=100.0, stop=95.0, target=103.0,
                        bias="LONG", horizon_min=60, cost_pct=0.06)
    assert out["hit_target"] is True and out["hit_stop"] is False


def test_win_requires_clearing_costs_not_merely_positive():
    flat = _bars().copy()
    for c in ("open", "high", "low", "close"):
        flat[c] = 100.01                       # +0.01%, below a 0.06% cost floor
    out = label_outcome(flat, TS, entry=100.0, stop=95.0, target=110.0,
                        bias="LONG", horizon_min=15, cost_pct=0.06)
    assert out["pnl_pct"] > 0
    assert out["directional_correct"] is False


def test_short_bias_inverts_the_comparisons():
    out = label_outcome(_bars(), TS, entry=100.0, stop=104.0, target=99.0,
                        bias="SHORT", horizon_min=60, cost_pct=0.06)
    assert out["hit_target"] is True
