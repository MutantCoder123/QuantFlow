"""Debounce-asymmetry fix (improved §4.6, Task 5.1 Step 4).

The gatekeeper loop used to gate BOTH escalation to the LLM and the plain
UI-card publish behind `if count < 3: continue`. That meant the first two
ticks of every advice transition left `latest_reports` stale or missing,
even though only the LLM call (expensive) needed the anti-flicker
protection -- showing the operator the current local-gatekeeper read is
cheap and should never be suppressed.

`ReasoningEngine.analysis_loop`'s full body needs a live TerminalDashboard,
ConvictionScorerRegistry, RegimeManagerRegistry, IntradayGatekeeper etc. to
run even one tick, so the debounce/publish decision was extracted into two
small classmethods that are exercised directly here instead:

  - `_advance_debounce(norm_sym, current_advice) -> bool` -- the stability
    counter, unchanged in behavior from before the fix.
  - `_publish_unstable(norm_sym, gatekeeper_res, payload)` -- the new
    always-publish path for a not-yet-stable tick, which is what actually
    fixes the starvation bug.

Also covers `_top_n_symbols` (Step 5), the pure selection function behind
the "escalate only the top N" attention gate.
"""
import json

from reasoning_engine import ReasoningEngine, ATTENTION_TOP_N


def _clear(norm_sym):
    ReasoningEngine.advice_debounce.pop(norm_sym, None)
    ReasoningEngine.last_math_advice.pop(norm_sym, None)
    ReasoningEngine.latest_reports.pop(norm_sym, None)
    ReasoningEngine.llm_enabled.pop(norm_sym, None)


def test_advance_debounce_requires_three_consecutive_ticks():
    _clear("DBTEST1")
    advice = {"action": "LONG", "auth": True, "has_pos": False}
    assert ReasoningEngine._advance_debounce("DBTEST1", advice) is False   # tick 1
    assert ReasoningEngine._advance_debounce("DBTEST1", advice) is False   # tick 2
    assert ReasoningEngine._advance_debounce("DBTEST1", advice) is True    # tick 3
    assert ReasoningEngine._advance_debounce("DBTEST1", advice) is True    # stays stable


def test_advance_debounce_resets_counter_on_a_new_advice():
    _clear("DBTEST2")
    a = {"action": "LONG", "auth": True, "has_pos": False}
    b = {"action": "SHORT", "auth": True, "has_pos": False}
    ReasoningEngine._advance_debounce("DBTEST2", a)
    ReasoningEngine._advance_debounce("DBTEST2", a)
    assert ReasoningEngine._advance_debounce("DBTEST2", a) is True   # stable on a
    assert ReasoningEngine._advance_debounce("DBTEST2", b) is False  # flip resets it


def test_unstable_tick_still_publishes_to_latest_reports_with_marker():
    """The core regression: an unstable tick must never leave latest_reports
    stale or missing -- it publishes the current read, marked unstable."""
    _clear("DBTEST3")
    ReasoningEngine.llm_enabled["DBTEST3"] = True
    gatekeeper_res = {"llm_authorized": True, "Action": "LONG"}
    ReasoningEngine._publish_unstable("DBTEST3", gatekeeper_res, {"current_time": "10:00:00"})

    assert "DBTEST3" in ReasoningEngine.latest_reports
    published = json.loads(ReasoningEngine.latest_reports["DBTEST3"])
    assert published["unstable"] is True
    assert published["Status_Tag"] == "STABILIZING"
    assert published["Generated_Time"] == "10:00:00"


def test_unstable_tick_marks_toggle_off_when_llm_disabled():
    _clear("DBTEST4")
    ReasoningEngine.llm_enabled["DBTEST4"] = False
    gatekeeper_res = {"llm_authorized": True, "Action": "LONG"}
    ReasoningEngine._publish_unstable("DBTEST4", gatekeeper_res, {"current_time": "10:00:00"})

    published = json.loads(ReasoningEngine.latest_reports["DBTEST4"])
    assert published["unstable"] is True
    assert published["Status_Tag"] == "REQUIRED LLM ANALYZE"


def test_unstable_tick_publishes_local_gatekeeper_reason_when_not_authorized():
    _clear("DBTEST5")
    gatekeeper_res = {"llm_authorized": False, "Action": "Wait", "math_rejection": "SOME_REASON"}
    ReasoningEngine._publish_unstable("DBTEST5", gatekeeper_res, {"current_time": "10:00:00"})

    published = json.loads(ReasoningEngine.latest_reports["DBTEST5"])
    assert published["unstable"] is True
    assert published["Reason"] == "SOME_REASON"


def test_top_n_symbols_selects_highest_ranked():
    ranks = [(0.5, "A"), (0.9, "B"), (0.1, "C"), (float("-inf"), "D"), (0.3, "E")]
    top = ReasoningEngine._top_n_symbols(ranks, n=2)
    assert top == {"B", "A"}


def test_top_n_symbols_excludes_rejected_setups():
    ranks = [(float("-inf"), "REJECTED"), (0.2, "OK1"), (0.1, "OK2")]
    top = ReasoningEngine._top_n_symbols(ranks, n=1)
    assert top == {"OK1"}


def test_attention_top_n_constant_matches_ui_grid_size():
    # No config file -- a plain constant, but it must stay in sync with the
    # "collapse below top 5" grid behavior described in the plan (§4.6).
    assert ATTENTION_TOP_N == 5
