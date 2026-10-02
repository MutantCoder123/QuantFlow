"""Policy v4, from the first three paper sessions (2026-09-29 .. 10-01):
run on past the target with a 1R trail, close when the live score has
opposed the position for 10 minutes (thesis exit), and journal every
decision as a REVIEW event."""
import pytest

from core.policy_config import load_policy
from paper.broker import PaperBroker
from paper.fills import Bar
from paper.store import EventStore, rebuild
from test_paper_broker_open import COSTS, Clock, Mirror, ist, make, sig

TRAIL = [[1.5, 0.0], [2.5, 1.0]]


def v4(tmp_path, t=ist(10, 0), run_on=1.0, thesis=0.15, review=5):
    """Entry 100, stop 98 (1R = 2), target 104 (2R)."""
    base, _, _ = make(tmp_path)
    clock = Clock(t)
    b = PaperBroker(store=EventStore(tmp_path / "events"), settings=base.settings, costs_cfg=COSTS,
                    clock=clock, mirror=Mirror(), entry_cutoff="13:40", square_off="15:10",
                    trail=TRAIL, reversal_cooldown_min=60, run_on_trail_r=run_on,
                    thesis_against=thesis, thesis_minutes=10, review_every_min=review)
    return b, clock


def bar(t, o, h, l):
    return Bar(ts=t, open=o, high=h, low=l, close=o)


def reviews(b):
    return [e for e in b.store.load() if e["type"] == "REVIEW"]


# ------------------------------------------------------------------ policy
def test_v4_holds_the_replayed_values():
    from paths import CONFIG_DIR
    p = load_policy(CONFIG_DIR / "policy_v4.yaml")                 # v5 is the default now; v4 kept as is
    assert p.version == 4
    assert p.horizon["run_on"] == {"trail_r": 1.0}
    assert p.horizon["thesis_exit"] == {"against": 0.15, "minutes": 10}
    assert p.horizon["review_every_min"] == 5
    assert p.horizon["entry_start_ist"] == "09:45"                    # v3's entry rules unchanged


def test_one_environment_variable_steps_back_to_v3(monkeypatch):
    monkeypatch.setenv("QUANTFLOW_POLICY", "policy_v3.yaml")
    load_policy.cache_clear()
    try:
        p = load_policy()
        assert p.version == 3 and "run_on" not in p.horizon
    finally:
        monkeypatch.delenv("QUANTFLOW_POLICY")
        load_policy.cache_clear()


# ------------------------------------------------------------------ run on
def test_at_the_target_the_trade_runs_on_with_the_stop_1R_below_it(tmp_path):
    b, _ = v4(tmp_path)
    b.on_signal(sig(target=104.0))
    b.mark("SAIL", 104.2)                                              # the loop marks, then checks
    assert b.check_touches("SAIL", None, ltp=104.2) is None            # target crossed: not closed
    p = b.position_for("SAIL")
    assert p["past_target"] is True
    assert p["trail_stop"] == pytest.approx(102.2)                     # best 2.1R - 1R = 1.1R
    # breakeven (v2 step at 1.5R) on the mark, then run on, then the run-on lock
    assert [r["kind"] for r in reviews(b)] == ["TRAIL", "RUN_ON", "TRAIL"]


def test_running_on_the_stop_follows_1R_behind_the_best_and_closes_as_run_on(tmp_path):
    b, _ = v4(tmp_path)
    b.on_signal(sig(target=104.0))
    b.check_touches("SAIL", None, ltp=104.0)
    b.mark("SAIL", 108.0)                                              # best 4R -> stop at 3R = 106
    assert b.position_for("SAIL")["trail_stop"] == pytest.approx(106.0)
    ev = b.check_touches("SAIL", None, ltp=105.9)
    assert ev["reason"] == "RUN_ON_STOP" and ev["exit_price"] == pytest.approx(105.9)
    assert ev["r_gross"] == pytest.approx(2.95)                        # beyond the 2R target


def test_a_bar_through_the_target_runs_on_and_only_later_bars_meet_the_new_stop(tmp_path):
    b, clock = v4(tmp_path)
    b.on_signal(sig(target=104.0))
    clock.t = ist(10, 20)
    assert b.check_touches("SAIL", [bar(ist(10, 5), 101, 104.5, 101.0)]) is None
    p = b.position_for("SAIL")
    assert p["past_target"] and p["trail_stop"] == pytest.approx(102.5)
    ev = b.check_touches("SAIL", [bar(ist(10, 10), 103, 103.2, 102.0)])
    assert ev["reason"] == "RUN_ON_STOP" and ev["exit_price"] == pytest.approx(102.5)


def test_without_run_on_the_target_still_closes(tmp_path):
    b, _ = v4(tmp_path, run_on=None)
    b.on_signal(sig(target=104.0))
    ev = b.check_touches("SAIL", None, ltp=104.2)
    assert ev["reason"] == "TARGET" and ev["exit_price"] == 104.0 and ev["touch_check"] == "ltp_only"


def test_a_short_runs_on_downwards(tmp_path):
    b, _ = v4(tmp_path)
    b.on_signal(sig(action="EXECUTE_SHORT", stop=102.0, target=96.0))
    b.mark("SAIL", 95.8)
    assert b.check_touches("SAIL", None, ltp=95.8) is None
    assert b.position_for("SAIL")["trail_stop"] == pytest.approx(97.8)  # best 2.1R - 1R


def test_a_restart_keeps_a_position_running_on(tmp_path):
    b, _ = v4(tmp_path)
    b.on_signal(sig(target=104.0))
    b.check_touches("SAIL", None, ltp=104.1)
    state = rebuild(b.store.load())
    (pos,) = state.open.values()
    assert pos["past_target"] is True and len(state.reviews) >= 1


# ------------------------------------------------------------------ thesis
def test_a_score_against_the_position_for_10_minutes_closes_it(tmp_path):
    b, clock = v4(tmp_path)
    b.on_signal(sig(target=108.0))
    b.mark("SAIL", 100.5)
    for minute in range(0, 10):
        clock.t = ist(10, 1 + minute)
        assert b.on_score("SAIL", -0.2, ltp=100.5) is None
    clock.t = ist(10, 11)
    ev = b.on_score("SAIL", -0.25, ltp=100.4)
    assert ev["reason"] == "THESIS_EXIT"
    kinds = [r["kind"] for r in reviews(b)]
    assert kinds[0] == "THESIS_AGAINST" and kinds[-1] == "THESIS_EXIT"


def test_a_score_that_recovers_restarts_the_clock(tmp_path):
    b, clock = v4(tmp_path)
    b.on_signal(sig(target=108.0))
    clock.t = ist(10, 1)
    b.on_score("SAIL", -0.3)
    clock.t = ist(10, 6)
    b.on_score("SAIL", 0.1)                                            # back on side
    clock.t = ist(10, 7)
    b.on_score("SAIL", -0.3)
    clock.t = ist(10, 12)
    assert b.on_score("SAIL", -0.3) is None                            # only 5 min against
    assert "THESIS_OK" in [r["kind"] for r in reviews(b)]


def test_a_mildly_negative_score_is_not_against_the_position(tmp_path):
    b, clock = v4(tmp_path)
    b.on_signal(sig(target=108.0))
    for minute in range(0, 15):
        clock.t = ist(10, 1 + minute)
        assert b.on_score("SAIL", -0.10) is None                       # above -0.15


def test_a_stale_score_neither_counts_nor_clears(tmp_path):
    b, clock = v4(tmp_path)
    b.on_signal(sig(target=108.0))
    clock.t = ist(10, 1)
    b.on_score("SAIL", -0.3)
    for minute in range(2, 12):
        clock.t = ist(10, minute)
        assert b.on_score("SAIL", -0.3, fresh=False) is None
    assert b.position_for("SAIL")["against_since"] == ist(10, 1)


def test_a_gap_in_scores_restarts_the_run(tmp_path):
    """A feed gap must not read as ten minutes of disagreement."""
    b, clock = v4(tmp_path)
    b.on_signal(sig(target=108.0))
    clock.t = ist(10, 1)
    b.on_score("SAIL", -0.3)
    clock.t = ist(10, 12)                                              # 11 min of silence
    assert b.on_score("SAIL", -0.3) is None
    assert b.position_for("SAIL")["against_since"] == ist(10, 12)


def test_a_short_is_opposed_by_a_positive_score(tmp_path):
    b, clock = v4(tmp_path)
    b.on_signal(sig(action="EXECUTE_SHORT", stop=102.0, target=92.0))
    for minute in range(0, 11):
        clock.t = ist(10, 1 + minute)
        ev = b.on_score("SAIL", 0.2, ltp=99.8)
    assert ev["reason"] == "THESIS_EXIT"


def test_without_a_thesis_rule_the_score_closes_nothing(tmp_path):
    b, clock = v4(tmp_path, thesis=None)
    b.on_signal(sig(target=108.0))
    for minute in range(0, 20):
        clock.t = ist(10, 1 + minute)
        assert b.on_score("SAIL", -0.9) is None


# ------------------------------------------------------------------ journal
def test_an_open_position_is_checked_every_five_minutes(tmp_path):
    b, clock = v4(tmp_path)
    b.on_signal(sig(target=108.0))
    b.mark("SAIL", 101.0)
    clock.t = ist(10, 4)
    b.on_score("SAIL", 0.3, regime="TREND_EXPANSION")
    assert reviews(b) == []
    clock.t = ist(10, 5)
    b.on_score("SAIL", 0.3, regime="TREND_EXPANSION")
    (r,) = reviews(b)
    assert r["kind"] == "CHECK" and r["decision"] == "hold"
    assert r["r_now"] == pytest.approx(0.5) and r["regime"] == "TREND_EXPANSION" and r["score"] == 0.3


def test_no_journal_without_the_policy(tmp_path):
    b, clock = v4(tmp_path, review=None)
    b.on_signal(sig(target=104.0))
    b.check_touches("SAIL", None, ltp=104.2)
    clock.t = ist(10, 30)
    b.on_score("SAIL", 0.3)
    assert reviews(b) == []
    assert b.position_for("SAIL")["past_target"]                       # run on works without it


def test_the_journal_is_in_the_equity_marks_and_the_live_summary(tmp_path):
    b, _ = v4(tmp_path)
    b.on_signal(sig(target=104.0))
    b.check_touches("SAIL", None, ltp=104.2)
    b.on_score("SAIL", 0.4)
    (o,) = b.summary()["open"]
    assert o["past_target"] and o["score_now"] == 0.4
    marks = b.equity_snapshot()["marks"]
    assert next(iter(marks.values()))["past_target"] is True


# ------------------------------------------------------------------ journal API
def test_the_journal_endpoint_returns_a_positions_open_reviews_and_close(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import api_server
    from paper import runtime as paper_rt
    b, clock = v4(tmp_path)
    monkeypatch.setattr(paper_rt, "_broker", b)
    b.on_signal(sig(target=104.0))
    b.mark("SAIL", 104.2)
    b.check_touches("SAIL", None, ltp=104.2)
    b.check_touches("SAIL", None, ltp=102.0)
    body = TestClient(api_server.app).get("/api/paper/journal", params={"symbol": "SAIL"}).json()
    types = [e["type"] for e in body["entries"]]
    assert types[0] == "OPEN" and types[-1] == "CLOSE" and "REVIEW" in types
    assert body["entries"][-1]["reason"] == "RUN_ON_STOP"
    assert all("settings" not in e and "costs" not in e for e in body["entries"])
    none = TestClient(api_server.app).get("/api/paper/journal", params={"symbol": "INFY"}).json()
    assert none["entries"] == []


# ------------------------------------------------------------------ restore
def test_a_stock_off_the_watchlist_is_not_restored_from_the_cache(tmp_path):
    """360ONE, 2026-10-01: removed from the watchlist, restored from
    cache_state.json, evaluated all day on a frozen price."""
    import json
    from rolling_state_engine import RollingStateEngine
    cache = tmp_path / "cache_state.json"
    rec = [{"timestamp": "2026-09-30 15:25:00", "close": 1.0}]
    cache.write_text(json.dumps({"dfs": {"NSE_EQ|SAIL": {"ltf_df": rec}, "NSE_EQ|360ONE": {"ltf_df": rec},
                                         "NSE_INDEX|Nifty 50": {"ltf_df": rec}}}))
    eng = RollingStateEngine.__new__(RollingStateEngine)
    eng.cache_file, eng.dfs = str(cache), {}
    eng.watchlist = {"2963": {"Token": "2963", "Symbol": "SAIL-EQ", "Exchange": "NSE"}}
    assert eng._hydrate_from_cache() is True
    assert set(eng.dfs) == {"NSE_EQ|SAIL", "NSE_INDEX|Nifty 50"}


def test_back_on_v3_a_position_past_its_target_closes_at_the_target_again(tmp_path):
    """The revert path (V4_CHANGES.md): restart under v3 with a run-on open."""
    b, _ = v4(tmp_path)
    b.on_signal(sig(target=104.0))
    b.mark("SAIL", 104.2)
    b.check_touches("SAIL", None, ltp=104.2)
    v3, _ = v4(tmp_path, run_on=None, thesis=None, review=None)
    v3.recover()
    assert v3.position_for("SAIL")["past_target"] is True
    ev = v3.check_touches("SAIL", None, ltp=104.1)
    assert ev["reason"] == "TARGET" and ev["exit_price"] == 104.0
