"""Shadow-mode both-arm logging (improved §4.5).

The whole architecture is premised on the LLM adding judgment over the math
layer, but only CONFIRM/ADJUST verdicts were ever journalled -- so ABORT and
DEFER vanished and the premise could never be tested. These records log both
arms on EVERY escalation, so the math-only counterfactual is scored even when
the LLM vetoed the trade.
"""
import json

import pandas as pd
import pytest

from journal.arms import ArmRecord, ArmJournal, label_arm_record, summarise_arms

MATH = {"action": "LONG", "entry": 100.0, "stop": 98.0, "target": 105.0}
LLM_CONFIRM = {"verdict": "CONFIRM", "action": "EXECUTE_LONG",
               "entry": 100.1, "stop": 98.0, "target": 105.0}
LLM_ABORT = {"verdict": "ABORT", "action": "PASS",
             "entry": None, "stop": None, "target": None}


def _rec(**kw):
    base = dict(symbol="SAIL", ts=1_757_000_000, config_version=1,
                math_arm=dict(MATH), llm_arm=dict(LLM_CONFIRM), escalated=True)
    base.update(kw)
    return ArmRecord(**base)


# --------------------------------------------------------------------------
# Journal
# --------------------------------------------------------------------------
def test_every_escalation_is_written_including_aborts(tmp_path):
    j = ArmJournal(tmp_path)
    j.write(_rec())
    j.write(_rec(symbol="INFY", llm_arm=dict(LLM_ABORT)))

    rows = j.load_all()
    assert len(rows) == 2
    verdicts = {r["llm_arm"]["verdict"] for r in rows}
    assert verdicts == {"CONFIRM", "ABORT"}, "ABORT verdicts must not vanish"


def test_record_carries_both_arms_and_is_jsonl(tmp_path):
    j = ArmJournal(tmp_path)
    j.write(_rec())
    files = list(tmp_path.rglob("*.jsonl"))
    assert files, "no JSONL written"
    line = files[0].read_text(encoding="utf-8").strip().splitlines()[0]
    row = json.loads(line)
    assert row["math_arm"]["entry"] == 100.0
    assert row["llm_arm"]["verdict"] == "CONFIRM"
    assert row["escalated"] is True


# --------------------------------------------------------------------------
# Labelling -- both arms, same resolver
# --------------------------------------------------------------------------
def _bars():
    """Price runs up to 103.5 without ever touching 98."""
    return pd.DataFrame({
        "timestamp": pd.to_datetime(["2025-09-04 17:23:20", "2025-09-04 17:28:20",
                                      "2025-09-04 17:33:20", "2025-09-04 17:38:20"]),
        "open":  [100.0, 100.5, 101.0, 102.0],
        "high":  [100.8, 101.5, 102.5, 103.5],
        "low":   [ 99.5, 100.1, 100.8, 101.9],
        "close": [100.5, 101.0, 102.0, 103.0],
    })


def test_math_arm_is_scored_even_when_the_llm_aborted():
    """The counterfactual is the whole point: an ABORTed proposal still gets
    a math-arm outcome, so LLM veto precision becomes measurable."""
    rec = _rec(llm_arm=dict(LLM_ABORT)).to_row()
    bars = _bars()
    bars["timestamp"] = pd.to_datetime(rec["ts"], unit="s") + pd.to_timedelta(
        [0, 5, 10, 15], unit="m")

    out = label_arm_record(rec, bars, horizon_min=90, cost_pct=0.06)

    assert out["math_outcome"] is not None
    assert out["math_outcome"]["directional_correct"] is True
    assert out["llm_outcome"] is None, "an aborted LLM arm took no trade"


def test_both_arms_scored_when_the_llm_confirmed():
    rec = _rec().to_row()
    bars = _bars()
    bars["timestamp"] = pd.to_datetime(rec["ts"], unit="s") + pd.to_timedelta(
        [0, 5, 10, 15], unit="m")

    out = label_arm_record(rec, bars, horizon_min=90, cost_pct=0.06)
    assert out["math_outcome"] is not None
    assert out["llm_outcome"] is not None


# --------------------------------------------------------------------------
# Summary / veto precision
# --------------------------------------------------------------------------
def _labelled(verdict, math_correct, math_r, llm_correct=None, llm_r=None):
    row = _rec(llm_arm={"verdict": verdict, "action": "X",
                        "entry": 100.0, "stop": 98.0, "target": 105.0}).to_row()
    row["math_outcome"] = {"directional_correct": math_correct, "r_multiple": math_r}
    row["llm_outcome"] = (None if llm_correct is None
                          else {"directional_correct": llm_correct, "r_multiple": llm_r})
    return row


def test_summary_reports_both_arms_separately():
    rows = [
        _labelled("CONFIRM", True, 1.5, True, 1.5),
        _labelled("CONFIRM", False, -1.0, False, -1.0),
        _labelled("ABORT", False, -1.0),          # LLM correctly vetoed a loser
        _labelled("ABORT", True, 2.0),            # LLM wrongly vetoed a winner
    ]
    s = summarise_arms(rows)

    assert s["math_only"]["n"] == 4
    assert s["math_only"]["win_rate"] == 50.0          # 2 of 4
    assert s["math_plus_llm"]["n"] == 2                # only the two taken
    assert s["math_plus_llm"]["win_rate"] == 50.0


def test_llm_veto_precision_is_the_share_of_aborts_that_would_have_lost():
    rows = [
        _labelled("ABORT", False, -1.0),   # good veto
        _labelled("ABORT", False, -0.5),   # good veto
        _labelled("ABORT", True, 2.0),     # bad veto
        _labelled("CONFIRM", True, 1.0, True, 1.0),
    ]
    s = summarise_arms(rows)
    assert s["n_vetoed"] == 3
    assert s["llm_veto_precision"] == pytest.approx(66.67, abs=0.01)


def test_veto_precision_is_none_with_no_vetoes():
    s = summarise_arms([_labelled("CONFIRM", True, 1.0, True, 1.0)])
    assert s["llm_veto_precision"] is None
    assert s["n_vetoed"] == 0
