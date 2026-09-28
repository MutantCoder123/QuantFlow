"""A whole paper session, hands-off, through the runtime entry points the
decision loop uses (on_ticket, on_gatekeeper, tick), with a process restart
in the middle.

Acceptance for Phase 2: every entry either opens or is rejected with a
reason; everything is closed by 15:20; a restart loses and double-counts
nothing; and the books reconcile exactly.
"""
import pytest

from paper import runtime as paper_rt
from paper.broker import PaperBroker
from paper.fills import Bar
from test_paper_broker_open import Mirror, ist, make

RAW = {
    "SAIL": {"token": "2963", "ltp": 100.0, "data_age_s": 1, "adv_shares": 5e6, "whale_cvd_ema_1h": 0},
    "IDEA": {"token": "14366", "ltp": 10.0, "data_age_s": 1, "adv_shares": 9e7, "whale_cvd_ema_1h": 0},
    "NMDC": {"token": "15332", "ltp": 70.0, "data_age_s": 1, "adv_shares": 2e7, "whale_cvd_ema_1h": 0},
}
TICKET = {"verdict": "CONFIRM", "institutional_rationale": "test"}
REGIME = {"current_regime": "TREND_EXPANSION", "session_phase": "MORNING_SESSION"}


def ticket(symbol, action, stop, target, ltp=None):
    raw = dict(RAW[symbol], **({"ltp": ltp} if ltp else {}))
    return paper_rt.on_ticket(symbol, dict(TICKET, action_directive=action),
                              {"final_stop": stop, "final_target": target},
                              {"composite_score": 0.6}, REGIME, raw, f"SIG-{symbol}", False)


async def test_a_full_hands_off_session_reconciles(tmp_path, monkeypatch):
    b, clock, mirror = make(tmp_path, t=ist(9, 30))
    monkeypatch.setattr(paper_rt, "_broker", b)
    prices = {"SAIL": 100.0, "IDEA": 10.0, "NMDC": 70.0}
    bars = {}

    def ltp_of(sym):
        return prices.get(sym), RAW[sym]["token"]

    async def fetch_bars(token):
        return bars.get(token)

    # 09:30 long SAIL, 09:45 short IDEA
    assert ticket("SAIL", "EXECUTE_LONG", 98.0, 104.0)["type"] == "OPEN"
    clock.t = ist(9, 45)
    assert ticket("IDEA", "EXECUTE_SHORT", 10.2, 9.6)["type"] == "OPEN"
    snap = await paper_rt.tick(ltp_of, fetch_bars)

    # 10:30 SAIL's bar reaches the target
    clock.t = ist(10, 30)
    prices["SAIL"] = 103.8
    bars["2963"] = [Bar(ist(10, 29), 103.0, 104.2, 102.9, 103.8)]
    snap = await paper_rt.tick(ltp_of, fetch_bars, snap)
    assert b.position_for("SAIL") is None and b.closed[-1]["reason"] == "TARGET"

    # 11:00 the web process restarts
    clock.t = ist(11, 0)
    b2 = PaperBroker(store=b.store, settings=b.settings, costs_cfg=b.costs_cfg, clock=clock,
                     mirror=Mirror(), entry_cutoff="13:45", square_off="15:20")
    assert b2.recover() == 0
    monkeypatch.setattr(paper_rt, "_broker", b2)
    assert [p["symbol"] for p in b2.open.values()] == ["IDEA"] and len(b2.closed) == 1
    assert b2.mirror["IDEA"]["direction"] == "Short"

    # 12:00 long NMDC; 13:00 Path A closes it (stop proximity)
    clock.t = ist(12, 0)
    assert ticket("NMDC", "EXECUTE_LONG", 69.0, 72.0)["type"] == "OPEN"
    clock.t = ist(13, 0)
    prices["NMDC"] = 69.3
    ev = paper_rt.on_gatekeeper("NMDC", {"Action": "Close", "Exit_Rule": "STOP_PROXIMITY"}, 69.3)
    assert ev["reason"] == "GATEKEEPER_STOP_PROXIMITY"

    # 13:50 a late signal is refused, with a reason
    clock.t = ist(13, 50)
    assert ticket("SAIL", "EXECUTE_LONG", 98.0, 104.0)["reason"] == "AFTER_ENTRY_CUTOFF"

    # 15:20 square-off takes IDEA
    clock.t = ist(15, 20)
    prices["IDEA"] = 9.9
    bars.pop("2963", None)
    await paper_rt.tick(ltp_of, fetch_bars, snap)
    assert not b2.open and not b2.mirror
    assert [t["reason"] for t in b2.closed] == ["TARGET", "GATEKEEPER_STOP_PROXIMITY", "SQUARE_OFF"]

    # Books reconcile exactly
    net = sum(t["net"] for t in b2.closed)
    last = b2.equity_snapshot()
    assert last["realized_total"] == pytest.approx(net, abs=1e-9)
    assert last["equity"] == pytest.approx(1_000_000 + net, abs=1e-9)
    assert last["unrealized"] == 0 and last["open_risk"] == 0
    for t in b2.closed:
        items = {k: v for k, v in t["costs"].items() if k != "total"}
        assert t["costs"]["total"] == pytest.approx(sum(items.values()), abs=1e-9)
        assert t["net"] == pytest.approx(t["gross"] - t["costs"]["total"], abs=1e-9)

    # And a fresh replay of the log agrees -- nothing lost, nothing counted twice
    from paper.store import rebuild
    state = rebuild(b2.store.load())
    assert len(state.closed) == 3 and not state.open
    assert sum(t["net"] for t in state.closed) == pytest.approx(net, abs=1e-9)
    assert [r["reason"] for r in state.rejections] == ["AFTER_ENTRY_CUTOFF"]


async def test_tick_errors_are_contained(tmp_path, monkeypatch):
    b, clock, _ = make(tmp_path)
    monkeypatch.setattr(paper_rt, "_broker", b)
    b.on_signal({"symbol": "SAIL", "token": "2963", "action": "EXECUTE_LONG", "ltp": 100.0,
                 "stop": 98.0, "target": 104.0, "adv_shares": 5e6, "data_age_s": 1})

    def broken_ltp(sym):
        raise RuntimeError("feed gone")

    await paper_rt.tick(broken_ltp)               # must not raise
    assert b.error_count == 1 and "feed gone" in b.last_error
    assert b.position_for("SAIL")                 # nothing closed by the failure


def test_the_dashboard_socket_carries_the_paper_block(tmp_path, monkeypatch):
    import api_server
    b, _, _ = make(tmp_path)
    monkeypatch.setattr(paper_rt, "_broker", b)
    blk = api_server._paper_live_block()
    assert blk["engine_ok"] is True and "open" in blk and "daily_loss_limit" in blk
    monkeypatch.setattr(paper_rt, "_broker", None)
    assert api_server._paper_live_block() == {"engine_ok": False, "running": False}
