"""The Signals tab's shaping (views.signals): ideas as runs, where each one
stops, the day by stock, and the queue's four-stage pipeline."""
from fastapi.testclient import TestClient

import api_server
from views.signals import funnel, idea_runs, order_queue, queue_row, timeline

T = 1790661000


def rec(sym, dt, decision):
    return {"ts": T + dt, "symbol": sym, "decision": decision}


def arm(sym, dt, verdict, action):
    return {"symbol": sym, "ts": T + dt, "llm_arm": {"verdict": verdict, "action": action}}


def test_ideas_are_runs_of_setups_split_by_a_gap():
    runs = idea_runs([rec("SAIL", 0, "PROPOSED"), rec("SAIL", 10, "GATED_UNKNOWN"), rec("SAIL", 20, "PROPOSED"),
                      rec("SAIL", 200, "GATED_REGIME_DAMPENED_LUNCH_CHOP"), rec("SAIL", 30, "REJECTED_NEUTRAL_CONVICTION"),
                      rec("IDEA", 5, "REJECTED_INSUFFICIENT_REWARD_RISK")])
    assert [(r["symbol"], r["start"] - T, r["end"] - T, r["proposed"]) for r in runs] == [("SAIL", 0, 20, True),
                                                                                       ("SAIL", 200, 200, False)]
    assert runs[1]["gates"] == {"REGIME_DAMPENED_LUNCH_CHOP": 1}


def test_the_funnel_follows_each_idea_to_where_it_stops():
    records = [rec("A", 0, "PROPOSED"),                  # reviewed, taken, opened
               rec("B", 0, "PROPOSED"),                  # reviewed, taken, rejected by paper
               rec("C", 0, "PROPOSED"),                  # reviewed, deferred
               rec("D", 0, "PROPOSED"),                  # never sent to the AI
               rec("E", 0, "GATED_REGIME_DAMPENED_LUNCH_CHOP"), rec("E", 10, "GATED_REGIME_DAMPENED_LUNCH_CHOP"),
               rec("F", 0, "GATED_UNKNOWN")]
    arms = [arm("A", 30, "CONFIRM", "EXECUTE_LONG"), arm("B", 150, "ADJUST", "EXECUTE_SHORT"),
            arm("C", 20, "DEFER", "PASS"), arm("C", 900, "CONFIRM", "EXECUTE_LONG")]   # C's later answer is another idea's
    events = [{"type": "OPEN", "symbol": "A", "ts": T + 35}, {"type": "REJECT", "symbol": "B", "ts": T + 155,
                                                               "reason": "SIZE_ROUNDS_TO_ZERO"}]
    f = funnel(records, arms, events)
    assert f["stages"] == {"found": 6, "cleared": 4, "reviewed": 3, "confirmed": 2, "opened": 1}
    assert f["drops"] == {"cleared": {"Held back by the session phase": 1, "Held by the gatekeeper": 1},
                          "reviewed": {"Not sent to the AI": 1}, "confirmed": {"AI deferred": 1},
                          "opened": {"SIZE_ROUNDS_TO_ZERO": 1}}
    assert f["ai_calls"] == 4
    assert funnel([], [], [])["stages"]["found"] == 0


def test_the_timeline_marks_verdicts_and_draws_holds():
    arms = [arm("A", 0, "CONFIRM", "EXECUTE_LONG"), arm("A", 60, "ABORT", "PASS"), arm("B", 0, "DEFER", "PASS")]
    events = [{"type": "OPEN", "pos_id": "p1", "symbol": "A", "ts": T + 5}, {"type": "CLOSE", "pos_id": "p1", "ts": T + 600, "net": -20},
              {"type": "OPEN", "pos_id": "p2", "symbol": "C", "ts": T + 700}]
    lanes = timeline(arms, events, T + 1000)
    assert [l["symbol"] for l in lanes] == ["A", "C", "B"]
    assert [m["kind"] for m in lanes[0]["marks"]] == ["taken", "rejected"]
    assert lanes[0]["holds"] == [{"start": T + 5, "end": T + 600, "kind": "loss"}]
    assert lanes[1]["holds"] == [{"start": T + 700, "end": T + 1000, "kind": "open"}]
    assert lanes[2]["marks"][0]["kind"] == "deferred"


SP = {"math_setup": {"setup_rejected": False, "directional_bias": "LONG", "composite_score": 0.36,
                     "execution_geometry": {"calculated_entry": 100, "padded_stop": 98, "calculated_target": 104},
                     "expectancy_matrix": {"reward_risk": 2.0}},
      "market_regime": {"current_regime": "TREND_EXPANSION"}}


def test_a_queue_row_resolves_the_four_stages():
    row = queue_row("MCX", SP, {"Status_Tag": "LLM_ANALYZED", "Action": "Long", "Qty": 465, "Reason": "Buyers absorbed.",
                                "Generated_Time": "11:32 am"}, None, None, 0.8)
    assert [s["tone"] for s in row["pipeline"]] == ["ok", "ok", "ok", "off"]
    assert [s["text"] for s in row["pipeline"]] == ["Long 0.36", "Sized 465", "AI: Long", ""]
    assert row["rr"] == 2.0 and row["entry"] == 100 and row["reason"] == "Buyers absorbed." and row["wants_action"]
    blocked = queue_row("TATAPOWER", SP, {"Risk_Rejection": "CLUSTER_LIMIT_POWER_CAPGOODS", "llm_authorized": True}, None,
                        None, 0.2)
    assert blocked["pipeline"][1] == {"tone": "bad", "text": "CLUSTER_LIMIT_POWER_CAPGOODS"} and blocked["blocked"]
    held = queue_row("SAIL", {"math_setup": {"setup_rejected": True}}, None,
                     {"side": "LONG", "entry_price": 180, "stop": 178, "target": 186, "last": 181, "r_now": 0.5, "ts": T},
                     None, None)
    assert held["pipeline"][0]["text"] == "Holding" and held["pipeline"][3]["text"] == "Open" and held["paper"]["entry"] == 180
    assert queue_row("IDEA", {"math_setup": {"setup_rejected": True}}, {"math_rejection": "NEUTRAL_CONVICTION"},
                     None, None, None) is None
    rejected = queue_row("NBCC", SP, None, None, {"reason": "ALREADY_OPEN"}, 0.1)
    assert rejected["pipeline"][3] == {"tone": "bad", "text": "ALREADY_OPEN"}


def test_the_queue_puts_holdings_first_then_attention():
    rows = [{"symbol": "A", "rank": 0.2, "composite": 0.3, "paper": None},
            {"symbol": "B", "rank": None, "composite": 0.5, "paper": None},
            {"symbol": "C", "rank": 0.9, "composite": 0.1, "paper": None},
            {"symbol": "D", "rank": None, "composite": 0.1, "paper": {"entry": 1}}]
    assert [r["symbol"] for r in order_queue(rows)] == ["D", "C", "A", "B"]


def test_the_queue_endpoint(monkeypatch):
    from reasoning_engine import ReasoningEngine
    monkeypatch.setattr(api_server, "local_active_states", {"NSE_EQ|MCX": {"symbol": "MCX", "ltp": 100.0},
                                                            "NSE_INDEX|Nifty 50": {}})
    monkeypatch.setattr(ReasoningEngine, "build_structured_payload", staticmethod(lambda sym, p, *a, **k: SP))
    monkeypatch.setattr(ReasoningEngine, "latest_reports", {"MCX": '{"Status_Tag": "PENDING_LLM"}'})
    monkeypatch.setattr(api_server, "_paper_events_today", lambda: [])
    body = TestClient(api_server.app).get("/api/signals/queue").json()
    assert body["status"] == "success" and body["quiet"] == 0
    assert body["rows"][0]["symbol"] == "MCX" and body["rows"][0]["pipeline"][2]["text"] == "AI reviewing"
