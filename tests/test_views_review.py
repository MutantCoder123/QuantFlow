"""The Review tab's shaping (views.review) and GET /api/review. Only signals
resolved at the horizon in force count; legacy ones are counted, not mixed in."""
from fastapi.testclient import TestClient

import api_server
from views.review import ci95, groups, histogram, measured, reliability, review, trend


def sig(day, correct, pnl, score=0.35, side="LONG", verdict="CONFIRM", regime="TREND_EXPANSION", phase="MORNING_SESSION",
        status="RESOLVED"):
    return {"session_date": day, "horizon": {"measure_at_minutes": [30, 90]},
            "signal_snapshot": {"composite_score": score, "bias": side, "verdict": verdict, "regime": regime,
                                "session_phase": phase},
            "outcome": {"status": status, "hit_stop": False, "hit_target": False, "pnl_30m_pct": pnl,
                        "directional_correct_30m": correct, "pnl_90m_pct": pnl, "directional_correct_90m": correct}}


LEGACY = {"session_date": "2026-06-17", "signal_snapshot": {"composite_score": 0.3, "bias": "LONG"},
          "outcome": {"status": "RESOLVED", "win_60m": True, "pnl_60m_pct": 0.4}}


def test_measured_counts_pending_and_legacy_apart():
    m = measured([sig("2026-09-29", True, 0.5), sig("2026-09-29", False, -0.2, status="PENDING"), LEGACY], [30, 90])
    assert len(m["rows"]) == 1 and m["pending"] == 1 and m["legacy"] == 1
    assert m["rows"][0] == {"date": "2026-09-29", "correct": True, "pnl": 0.5, "side": "LONG", "verdict": "CONFIRM",
                            "regime": "TREND_EXPANSION", "phase": "MORNING_SESSION", "score": 0.35}


def test_trend_is_daily_with_a_weighted_rolling_rate():
    rows = [{"date": "2026-09-28", "correct": True}] * 3 + [{"date": "2026-09-28", "correct": False}] \
        + [{"date": "2026-09-29", "correct": False}] * 4
    t = trend(rows, sessions=20, window=5)
    assert [(d["date"], d["n"], d["hit_rate"], d["rolling"], d["thin"]) for d in t] == [
        ("2026-09-28", 4, 75.0, 75.0, True), ("2026-09-29", 4, 0.0, 37.5, True)]
    assert len(trend(rows, sessions=1)) == 1


def test_histogram_bins_the_move_and_clamps_the_ends():
    h = histogram([{"pnl": v} for v in (-3.0, -0.1, 0.0, 0.1, 0.3, 5.0)])
    assert h["n"] == 6 and h["right"] == 3 and h["wrong"] == 3
    assert h["bins"][0]["n"] == 1 and h["bins"][-1]["n"] == 1           # -3 and +5 land in the end bins
    assert h["median"] == 0.05
    assert histogram([])["median"] is None


def test_groups_flag_thin_buckets():
    rows = [{"side": "LONG", "verdict": "CONFIRM", "regime": "TREND_EXPANSION", "phase": "MORNING_SESSION", "correct": i % 2 == 0}
            for i in range(12)] + [{"side": "SHORT", "verdict": "ABORT", "regime": None, "phase": "LUNCH_CHOP", "correct": True}]
    g = groups(rows)
    assert [p["title"] for p in g] == ["By side", "By AI verdict", "By regime", "By session phase"]
    assert g[0]["rows"][0] == {"label": "Long", "n": 12, "hit_rate": 50.0, "thin": False}
    assert g[0]["rows"][1]["thin"] is True
    assert g[1]["rows"][1]["label"] == "Reject" and g[2]["rows"][1]["label"] == "Unknown"
    assert g[3]["rows"][0]["label"] == "Morning session"


def test_reliability_has_a_95pc_range_and_hides_thin_buckets():
    rows = [{"score": 0.25, "correct": i < 6} for i in range(10)] + [{"score": 0.55, "correct": True}] * 3 \
        + [{"score": None, "correct": True}]
    r = reliability(rows)
    b = r["buckets"]
    assert (b[2]["n"], b[2]["hit_rate"], b[2]["ci"]) == (10, 60.0, ci95(6, 10))
    assert ci95(6, 10) == 30.4
    assert b[5]["n"] == 3 and b[5]["hit_rate"] is None and b[5]["thin"]
    assert r["shown"] == 1 and r["spread"] is None
    assert ci95(0, 0) is None


def test_review_over_sessions_or_one_date():
    s = [sig("2026-09-28", True, 0.4), sig("2026-09-29", False, -0.3), sig("2026-09-29", True, 0.2)]
    whole = review(s, sessions=20, measure_at=[30, 90])
    assert (whole["sessions"], whole["n"], whole["hit_rate"]) == (2, 3, 66.7)
    one = review(s, date="2026-09-29", measure_at=[30, 90])
    assert (one["n"], one["hit_rate"]) == (2, 50.0)
    last = review(s, sessions=1, measure_at=[30, 90])
    assert last["n"] == 2 and last["sessions"] == 1


def test_the_endpoint(monkeypatch):
    from signal_ledger import SignalLedger
    monkeypatch.setattr(SignalLedger, "load_all_signals", classmethod(lambda cls, n=30: [sig("2026-09-29", True, 0.4)]))
    c = TestClient(api_server.app)
    body = c.get("/api/review?sessions=5").json()
    assert body["status"] == "success" and body["n"] == 1
    assert c.get("/api/review?date=29-09-2026").json()["status"] == "error"
