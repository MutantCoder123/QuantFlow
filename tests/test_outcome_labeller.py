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


# 10:00 IST: the feed stamps its bars in naive IST, and a signal carries an epoch
TS = int(pd.Timestamp("2026-09-09 10:00", tz="Asia/Kolkata").timestamp())


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


def test_an_epoch_is_read_on_the_bars_ist_clock():
    """2026-09-29: the window was built from the epoch as naive UTC, 5 h 30 m
    before the IST bars, so every live outcome came back NO_DATA (and was stored
    as a 0% loss). 12:20 IST must find the 12:20 bar."""
    bars = pd.DataFrame({"timestamp": pd.to_datetime(["2026-09-29 12:20", "2026-09-29 12:25"]),
                         "open": [100.0, 101.0], "high": [101.0, 104.0], "low": [99.5, 100.5], "close": [101.0, 103.5]})
    ts = int(pd.Timestamp("2026-09-29 12:20", tz="Asia/Kolkata").timestamp())
    out = label_outcome(bars, ts, entry=100.0, stop=98.0, target=103.0, bias="LONG", horizon_min=90, cost_pct=0.06)
    assert out["outcome"] == "TARGET" and out["bars_seen"] == 2
    aware = bars.assign(timestamp=bars["timestamp"].dt.tz_localize("Asia/Kolkata"))
    assert label_outcome(aware, ts, 100.0, 98.0, 103.0, "LONG", 90, 0.06)["outcome"] == "TARGET"


def test_no_bars_in_the_window_is_no_data():
    ts = int(pd.Timestamp("2026-09-10 10:00", tz="Asia/Kolkata").timestamp())
    assert label_outcome(_bars(), ts, 100.0, 99.0, 105.0, "LONG", 60, 0.06)["outcome"] == "NO_DATA"
