"""Breakdowns and diagnostics over the hand-computed four-trade book in
test_performance_metrics (see its docstring for the figures)."""
import pytest

from performance.breakdowns import all_breakdowns, breakdown, score_bucket
from performance.diagnostics import (all_diagnostics, calibration, costs, excursions,
                                     exit_economics, llm_value, match_arm, r_histogram, rejections)
from test_performance_metrics import at, scope, val


def trades():
    return scope().trades


def row(rows, label):
    return next(r for r in rows if r["label"] == label)


# ------------------------------------------------------------- breakdowns
def test_score_buckets():
    assert score_bucket(0.35) == "0.3-0.4" and score_bucket(-0.25) == "0.2-0.3"
    assert score_bucket(0.0) == "0.0-0.1" and score_bucket(0.73) == "0.5+"
    assert score_bucket(None) is None and score_bucket("x") is None


def test_breakdown_by_regime_with_hand_figures():
    rows = breakdown(trades(), "regime", 1)
    trend, chop = row(rows, "TREND_EXPANSION"), row(rows, "LUNCH_CHOP")
    assert trend["n"] == 2 and val(trend["net"]) == pytest.approx(1300)
    assert val(trend["win_rate"]) == 100.0 and val(trend["expectancy_r"]) == pytest.approx(1.3)
    assert chop["n"] == 2 and val(chop["net"]) == pytest.approx(-700)
    assert val(chop["win_rate"]) == 0.0


def test_buckets_below_minimum_keep_sums_but_withhold_rates():
    trend = row(breakdown(trades(), "regime", 10), "TREND_EXPANSION")
    assert val(trend["net"]) == pytest.approx(1300)
    assert trend["win_rate"] == {"value": None, "n": 2, "min_n": 10, "status": "insufficient"}


def test_every_dimension_and_ordinal_order():
    b = all_breakdowns(trades(), 1)
    assert set(b) == {"regime", "session_phase", "symbol", "cluster", "direction", "exit_reason",
                      "score_bucket", "verdict", "hour", "config_version", "regime_side"}
    assert row(b["regime_side"], "LONG|TREND_EXPANSION")["n"] == 2
    assert row(b["regime_side"], "SHORT|LUNCH_CHOP")["n"] == 1
    assert [r["label"] for r in b["hour"]] == ["09:00", "11:00", "13:00"]
    assert [r["label"] for r in b["score_bucket"]] == ["0.1-0.2", "0.2-0.3", "0.3-0.4", "0.4-0.5"]
    assert row(b["direction"], "SHORT")["n"] == 1 and row(b["verdict"], "ADJUST")["n"] == 1
    assert row(b["config_version"], "1")["n"] == 4
    for dim in b.values():                                   # nothing dropped
        assert sum(r["n"] for r in dim) == 4


def test_unknown_values_are_a_bucket_not_dropped():
    ts = trades()
    ts[0] = dict(ts[0], context={})
    rows = breakdown(ts, "regime", 1)
    assert row(rows, "unknown")["key"] is None and row(rows, "unknown")["n"] == 1


def test_unknown_dimension_is_refused():
    with pytest.raises(ValueError):
        breakdown(trades(), "weather", 1)


# --------------------------------------------------------- excursions
def test_excursions_by_hand():
    e = excursions(trades(), 1)
    assert val(e["avg_mae_winners"]) == pytest.approx((-0.2 - 0.9) / 2)
    assert val(e["avg_mfe_losers"]) == pytest.approx((0.6 + 0.1) / 2)
    assert val(e["stops_after_half_r_up_pct"]) == pytest.approx(100.0)     # T2 was +0.6R first
    assert val(e["wins_near_stop_pct"]) == pytest.approx(50.0)             # T3 dipped to -0.9R
    assert val(e["reached_target_pct"]) == pytest.approx(25.0)             # only T1 saw +2R
    assert val(e["avg_given_back_r"]) == pytest.approx((0 + 1.5 + 0.36 + 0.08) / 4)
    assert val(e["winner_capture_pct"]) == pytest.approx((1.0 + 0.64) / 2 * 100)
    assert len(e["points"]) == 4 and "gates.stop_proximity_pct" in e["tunes"]
    assert len(e["tunes"]) == len(set(e["tunes"]))


def test_calibration_buckets_and_monotonic_check():
    c = calibration(trades(), 1)
    assert [r["bucket"] for r in c["buckets"]] == ["0.1-0.2", "0.2-0.3", "0.3-0.4", "0.4-0.5"]
    assert [r["win_rate"]["value"] for r in c["buckets"]] == [0.0, 0.0, 100.0, 100.0]
    assert c["monotonic"] is True
    assert calibration(trades(), 15)["monotonic"] is None       # can't say yet


def test_exit_economics_shares_and_tunes():
    x = exit_economics(trades(), 1)
    tgt = next(r for r in x["rows"] if r["key"] == "TARGET")
    assert tgt["share_of_net_pct"] == pytest.approx(1000 / 600 * 100)
    assert x["total_net"] == pytest.approx(600)
    flip = next(r for r in x["rows"] if r["key"] == "GATEKEEPER_WHALE_FLIP")
    assert flip["tunes"] == ["gates.whale_flip_adv_frac"]


def test_costs_by_hand():
    c = costs(trades(), 1)
    assert val(c["total"]) == pytest.approx(380)
    assert sum(c["items"].values()) == pytest.approx(380)                  # itemised = total
    assert val(c["per_trade"]) == pytest.approx(95)
    assert val(c["slippage"]) == pytest.approx(4 * (0.03 + 0.02) * 500)
    assert val(c["flipped_to_loss"]) == 1 and c["flipped_symbols"] == ["SAIL"]   # T4


def test_r_histogram_puts_every_trade_in_one_bin():
    h = r_histogram(trades())
    assert sum(b["n"] for b in h) == 4
    hit = {(b["lo"], b["hi"]): b["n"] for b in h if b["n"]}
    assert hit == {(2.0, 2.5): 1, (-1.0, -0.5): 1, (0.5, 1.0): 1, (-0.5, 0.0): 1}


def test_rejections_grouped_with_their_knob():
    r = rejections([{"reason": "CLUSTER_LIMIT_PSU"}, {"reason": "CLUSTER_LIMIT_BANKS"},
                    {"reason": "AFTER_ENTRY_CUTOFF"}])
    assert r[0] == {"reason": "CLUSTER_LIMIT", "n": 2, "tunes": ["risk.max_cluster_risk_pct"]}
    assert r[1]["tunes"] == ["horizon.entry_cutoff_ist"]


# ---------------------------------------------------------- AI value-add
def arms():
    t1 = trades()[0]                                   # SAIL, signal ts = open - 5 s
    sig_ts = int(t1["signal_id"].rsplit("-", 1)[1])
    return [
        {"symbol": "SAIL", "ts": sig_ts - 2, "llm_arm": {"verdict": "CONFIRM"},
         "math_outcome": {"outcome": "TARGET", "r_multiple": 1.5}},
        {"symbol": "IDEA", "ts": 1, "llm_arm": {"verdict": "ABORT"},
         "math_outcome": {"outcome": "STOP", "r_multiple": -1.0}},
        {"symbol": "NMDC", "ts": 2, "llm_arm": {"verdict": "DEFER"},
         "math_outcome": {"outcome": "TIMEOUT", "r_multiple": -0.5}},
        {"symbol": "SAIL", "ts": 3, "llm_arm": {"verdict": "ABORT"},
         "math_outcome": {"outcome": "NO_DATA", "r_multiple": 0.0}},
    ]


def test_ai_value_compares_vetoes_to_what_was_taken():
    v = llm_value(trades(), arms(), 1)
    assert v["vetoed"]["n"] == 3 and v["vetoed"]["labelled"] == 2      # NO_DATA isn't a result
    assert val(v["vetoed"]["avg_math_r"]) == pytest.approx(-0.75)
    assert val(v["taken"]["avg_math_r"]) == pytest.approx(1.5)
    assert v["taken"]["matched_paper_trades"] == 1
    assert val(v["taken"]["avg_paper_r"]) == pytest.approx(2.0)
    assert {r["label"] for r in v["by_verdict"]} == {"CONFIRM", "ADJUST"}
    assert "counterfactual" in v["note"]


def test_arm_matching_needs_same_symbol_and_close_time():
    t1 = trades()[0]
    assert match_arm(t1, arms()) is not None
    far = [dict(arms()[0], ts=arms()[0]["ts"] + 3600)]
    assert match_arm(t1, far) is None
    assert match_arm(dict(t1, signal_id=None), arms()) is None
    # another symbol's escalation at the very same second is not this trade's
    sig_ts = int(t1["signal_id"].rsplit("-", 1)[1])
    other = {"symbol": "NMDC", "ts": sig_ts, "llm_arm": {"verdict": "CONFIRM"}}
    assert match_arm(t1, [other]) is None
    assert match_arm(t1, [other, arms()[0]])["symbol"] == "SAIL"


def test_all_diagnostics_shape():
    d = all_diagnostics(trades(), [], [], {"rates": 20, "bucket": 10, "calibration": 15})
    assert set(d) == {"excursions", "calibration", "exits", "costs", "r_histogram", "rejections", "ai"}
    assert d["excursions"]["avg_mae_winners"]["status"] == "insufficient"
    assert d["ai"]["vetoed"]["avg_math_r"]["status"] == "insufficient"
