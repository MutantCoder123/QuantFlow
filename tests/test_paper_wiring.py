"""The real decision path opens and closes paper positions.

Drives ReasoningEngine.analyze_stock end to end with only the model call,
the signal-ledger file write and the arms journal stubbed. Asserting on the
broker alone would pass even if the hook in reasoning_engine were never
reached -- this test fails unless the wiring itself works.
"""
import json

import pytest

import discovery_analysis
from diagnostic_ui import TerminalDashboard
from paper import runtime as paper_rt
from reasoning_engine import ReasoningEngine
from signal_ledger import SignalLedger
from test_paper_broker_open import make


def _ticket(action, verdict="CONFIRM"):
    return json.dumps({"execution_ticket": {
        "verdict": verdict, "action_directive": action,
        "institutional_rationale": "trend with absorption", "risk_parameters": {}}})


PAYLOAD = {
    "math_setup": {"setup_rejected": False, "composite_score": 0.6, "directional_bias": "LONG",
                   "execution_geometry": {"calculated_entry": 100.0, "padded_stop": 98.0,
                                          "calculated_target": 104.0},
                   "expectancy_matrix": {}},
    "market_regime": {"current_regime": "TREND_EXPANSION", "session_phase": "MORNING_SESSION"},
    "ltp": 100.0, "current_time": "10:00:00", "atr_15m": 1.0,
}


@pytest.fixture
def wired(tmp_path, monkeypatch):
    b, clock, _ = make(tmp_path)
    positions = {}
    b.mirror = positions
    monkeypatch.setattr(ReasoningEngine, "user_positions", positions)
    monkeypatch.setattr(paper_rt, "_broker", b)
    monkeypatch.setattr(TerminalDashboard, "active_states", {"2963": {
        "symbol": "SAIL", "token": "2963", "ltp": 100.0, "data_age_s": 1.0,
        "adv_shares": 5_000_000.0, "whale_cvd_ema_1h": 0.0}})
    monkeypatch.setattr(SignalLedger, "record_signal", classmethod(lambda cls, **kw: "SIG-TEST-1"))
    monkeypatch.setattr(ReasoningEngine, "_write_arm_record", classmethod(lambda cls, *a, **k: None))
    reply = {"text": _ticket("EXECUTE_LONG")}

    def fake_generator(model, client, http_post):
        async def gen(prompt):
            return reply["text"]
        return gen
    monkeypatch.setattr(discovery_analysis, "_generator", fake_generator)
    return b, positions, reply


async def test_a_confirmed_ticket_opens_a_paper_position(wired):
    b, positions, _ = wired
    await ReasoningEngine.analyze_stock("SAIL", "ollama:stub", "", is_autonomous=True,
                                        precomputed_payload=dict(PAYLOAD))
    pos = b.position_for("SAIL")
    assert pos is not None, f"no paper position; last_error={b.last_error}"
    assert pos["signal_id"] == "SIG-TEST-1"
    assert pos["stop"] == 98.0 and pos["target"] == 104.0          # the clamped geometry
    assert positions["SAIL"]["source"] == "paper"


async def test_a_close_ticket_closes_it(wired):
    b, positions, reply = wired
    await ReasoningEngine.analyze_stock("SAIL", "ollama:stub", "", is_autonomous=True,
                                        precomputed_payload=dict(PAYLOAD))
    reply["text"] = _ticket("CLOSE_EXISTING")
    await ReasoningEngine.analyze_stock("SAIL", "ollama:stub", "", is_autonomous=True,
                                        precomputed_payload=dict(PAYLOAD))
    assert b.position_for("SAIL") is None
    assert b.closed[-1]["reason"] == "LLM_CLOSE" and "SAIL" not in positions


async def test_an_abort_opens_nothing(wired):
    b, _, reply = wired
    reply["text"] = _ticket("EXECUTE_LONG", verdict="ABORT")
    await ReasoningEngine.analyze_stock("SAIL", "ollama:stub", "", is_autonomous=True,
                                        precomputed_payload=dict(PAYLOAD))
    assert b.position_for("SAIL") is None and not b.store.load()


async def test_a_manual_position_blocks_the_paper_entry(wired):
    b, positions, _ = wired
    positions["SAIL"] = {"direction": "Long", "entry_price": 99.0, "qty": 10}   # entered by hand
    await ReasoningEngine.analyze_stock("SAIL", "ollama:stub", "", is_autonomous=True,
                                        precomputed_payload=dict(PAYLOAD))
    assert b.position_for("SAIL") is None
    assert b.store.load()[-1]["reason"] == "MANUAL_POSITION_HELD"
    assert "source" not in positions["SAIL"]                      # the manual entry is untouched


def test_the_breaker_reads_paper_losses(wired):
    b, _, _ = wired
    b.on_signal({"symbol": "SAIL", "token": "2963", "action": "EXECUTE_LONG", "ltp": 100.0,
                 "stop": 98.0, "target": 104.0, "adv_shares": 5e6, "data_age_s": 1})
    b.check_touches("SAIL", bars=None, ltp=97.5)                  # stopped out
    assert paper_rt.realized_loss_today() > 0
    assert ReasoningEngine._build_portfolio().realized_loss_today == paper_rt.realized_loss_today()


@pytest.mark.parametrize("directive", ["CLOSE_EXISTING", "REVERSE_POSITION"])
async def test_a_position_directive_with_no_position_is_not_a_signal(wired, monkeypatch, directive):
    """2026-09-29: the local judge said CLOSE_EXISTING, then REVERSE_POSITION,
    for a flat BHEL; both became ledger signals (SHORT, no geometry) and alerts."""
    b, _, reply = wired
    recorded = []
    monkeypatch.setattr(SignalLedger, "record_signal", classmethod(lambda cls, **kw: recorded.append(kw) or "SIG-X"))
    alerts_before = len(ReasoningEngine.global_alerts)
    reply["text"] = _ticket(directive)
    await ReasoningEngine.analyze_stock("SAIL", "ollama:stub", "", is_autonomous=True,
                                        precomputed_payload=dict(PAYLOAD))
    assert recorded == [] and not b.store.load()
    assert len(ReasoningEngine.global_alerts) == alerts_before
