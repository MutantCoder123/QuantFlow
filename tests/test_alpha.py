"""Policy v7: the alpha score picks entries; the v5 composite runs beside it (alpha/)."""
import json
import time
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

CFG = {"entry_scorer": True, "select_frac": 0.10, "max_age_min": 12.0}
SETUP = {"directional_bias": "SHORT", "composite_score": -0.31, "setup_rejected": True,
         "rejection_reason": "INSUFFICIENT_REWARD_RISK", "execution_geometry": {}, "expectancy_matrix": {}}
PAYLOAD = {"ltp": 100.0, "atr_15m": 0.8}


def _live(rank, ts=None, sym="SAIL"):
    return {"ts": ts or time.time() - 60, "time": "10:00",
            "scores": {sym: {"score": 0.01, "rank": rank, "strength": (rank - 0.5) * 2}}}


def test_a_top_ranked_stock_is_a_long_with_the_researched_geometry():
    from alpha.gate import apply
    out = apply(SETUP, PAYLOAD, "NSE_EQ|SAIL", CFG, live=_live(0.95), tod=1.2)
    assert out["setup_rejected"] is False and out["directional_bias"] == "LONG"
    g = out["execution_geometry"]
    assert g["calculated_entry"] == 100.0
    assert g["padded_stop"] == pytest.approx(100 - 1.5 * 0.8 * 1.2, abs=0.01)
    assert g["calculated_target"] == pytest.approx(100 + 1.5 * 1.5 * 0.8 * 1.2, abs=0.01)
    assert out["composite_score"] == pytest.approx(0.9)
    # the composite rides along, untouched, for the log and the exits
    assert out["v5_composite"] == -0.31 and out["v5_bias"] == "SHORT" and out["scorer"] == "ALPHA"


def test_a_bottom_ranked_stock_is_a_short():
    from alpha.gate import apply
    out = apply(SETUP, PAYLOAD, "SAIL", CFG, live=_live(0.04), tod=1.0)
    assert out["directional_bias"] == "SHORT" and out["execution_geometry"]["padded_stop"] == pytest.approx(101.2)


@pytest.mark.parametrize("live,reason", [
    (_live(0.5), "ALPHA_NOT_SELECTED"),
    (_live(0.95, ts=time.time() - 13 * 60), "ALPHA_STALE"),
    ({}, "ALPHA_STALE"),
    (_live(0.95, sym="NMDC"), "ALPHA_NO_SCORE"),
])
def test_no_entry_unless_freshly_selected(live, reason):
    from alpha.gate import apply
    out = apply(SETUP, PAYLOAD, "SAIL", CFG, live=live, tod=1.0)
    assert out["setup_rejected"] is True and out["rejection_reason"] == reason
    assert out["v5_composite"] == -0.31


def test_policy_v7_only_adds_the_alpha_entry_scorer():
    from core.policy_config import load_policy
    from paths import CONFIG_DIR
    v52, v7 = load_policy(CONFIG_DIR / "policy_v5_2.yaml"), load_policy(CONFIG_DIR / "policy_v7.yaml")
    assert v7.version == 7
    assert v7.conviction["alpha"] == {"entry_scorer": True, "select_frac": 0.10, "max_age_min": 12}
    assert {k: v for k, v in v7.conviction.items() if k != "alpha"} == v52.conviction
    assert (v7.horizon, v7.gates, v7.semantic, v7.regime) == (v52.horizon, v52.gates, v52.semantic, v52.regime)


def test_the_gatekeeper_authorises_an_alpha_setup(monkeypatch):
    """An alpha LONG passes the gatekeeper's entry path (edge, score, data) in the window."""
    from alpha.gate import apply
    from intraday_gatekeeper import IntradayGatekeeper
    from core.policy_config import load_policy
    from paths import CONFIG_DIR
    pol = load_policy(CONFIG_DIR / "policy_v7.yaml")
    monkeypatch.setattr("core.policy_config.load_policy", lambda *a, **k: pol)
    monkeypatch.setattr("core.data_readiness.missing", lambda p: [])
    ms = apply(SETUP, PAYLOAD, "SAIL", CFG, live=_live(0.97), tod=1.0)
    structured = {"market_regime": {"current_regime": "TREND_EXPANSION", "session_phase": "MORNING_SESSION"},
                  "math_setup": ms}
    with __import__("freezegun").freeze_time("2026-10-15 05:30:00"):          # 11:00 IST
        res = IntradayGatekeeper.evaluate(structured, {"ltp": 100.0, "market_state": "LIVE", "data_age_s": 1.0},
                                          {}, 100.0)
    assert res.get("llm_authorized"), res


def test_build_payload_uses_alpha_for_entries_but_the_composite_for_a_held_position(monkeypatch):
    from reasoning_engine import ReasoningEngine
    monkeypatch.setattr("alpha.gate.config", lambda: dict(CFG))
    monkeypatch.setattr("alpha.gate.live_scores", lambda: _live(0.96))
    monkeypatch.setattr("alpha.gate._tod", lambda s: 1.0)
    payload = {"symbol": "SAIL", "token": "NSE_EQ|SAIL", "ltp": 100.0, "atr_15m": 0.8, "atr_5m": 0.3}
    sp = ReasoningEngine.build_structured_payload("SAIL", dict(payload), None)
    assert sp["math_setup"]["scorer"] == "ALPHA" and sp["math_setup"]["directional_bias"] == "LONG"
    assert "v5_composite" in sp["math_setup"]
    held = ReasoningEngine.build_structured_payload("SAIL", dict(payload),
                                                    {"direction": "Long", "entry_price": 99.0, "qty": 1})
    assert held["math_setup"].get("scorer") != "ALPHA"


def test_watchlist_selection_pins_first_then_alternates_long_and_short_among_liquid_stocks():
    from alpha.select_watchlist import select
    S = pd.DataFrame({"score": [0.9, 0.8, 0.1, -0.7, -0.9, 0.0]}, index=["A", "B", "C", "D", "E", "F"])
    adv = {"A": 100, "B": 10, "C": 100, "D": 100, "E": 100, "F": 100}.get     # B is illiquid
    assert select(S, ["F"], 4, 50, adv=adv) == ["F", "A", "E", "C"]          # long A, short E, long C (B illiquid)
    assert len(select(S, [], 10, 50, adv=adv)) == 5                          # all liquid ones, no repeats


def test_today_bars_come_from_completed_minutes_only():
    from alpha.service import today_arrays
    c = [[f"2026-10-15T09:{m:02d}:00+05:30", 100 + i, 101 + i, 99 + i, 100.5 + i, 10] for i, m in enumerate(range(15, 26))]
    arr = today_arrays(["SAIL"], {"SAIL": c}, datetime(2026, 10, 15, 9, 25, 30, tzinfo=__import__("zoneinfo").ZoneInfo("Asia/Kolkata")))
    assert arr["v"][0, 0] == 50 and arr["v"][0, 1] == 50          # 09:15-09:19, 09:20-09:24
    assert np.isnan(arr["c"][0, 2]) or arr["v"][0, 2] == 0        # 09:25 not complete yet
    assert arr["o"][0, 0] == 100 and arr["c"][0, 1] == pytest.approx(109.5)


def test_judge_ab_arms_come_from_paper_yaml():
    from paper.settings import PaperSettings
    arms = PaperSettings().options()["judge_ab"]
    assert arms == ["groq:openai/gpt-oss-120b", "ollama:qwen2.5:7b"]


def test_an_alpha_trade_ignores_the_shadow_composite_for_exits(tmp_path):
    """10-15: CANBK, an alpha short (strength -0.96), was told 'the score now opposes the short'
    by the composite (+0.5): the thesis exit would have closed it in 10 minutes, and the fade
    compared the composite with the alpha strength. Alpha trades keep stop/target/run on only."""
    from test_policy_v4 import v4, reviews
    from test_paper_broker_open import ist, sig
    from paper.store import rebuild
    b, clock = v4(tmp_path)
    b.fade_frac, b.fade_s = 0.5, 600.0
    b.on_signal(sig(target=108.0, composite=0.96, scorer="ALPHA", v5_composite=-0.31))
    b.mark("SAIL", 101.5)
    for minute in range(0, 25):
        clock.t = ist(10, 1 + minute)
        assert b.on_score("SAIL", -0.5, ltp=101.5) is None                  # composite against: no exit
    p = b.position_for("SAIL")
    assert p["context"]["scorer"] == "ALPHA" and b._stop_now(p) < 100.0     # no fade to breakeven
    assert {r["kind"] for r in reviews(b)} <= {"CHECK", "TRAIL"}
    assert reviews(b)[-1]["score_at_entry"] == -0.31                        # same scale as the score
    # a v7 position opened before the context carried the scorer is an alpha entry too
    (pos,) = rebuild(b.store.load()).open.values()
    pos["context"].pop("scorer")
    pos["config_version"] = 7
    from paper.broker import _alpha_entry
    assert _alpha_entry(pos) and not _alpha_entry(dict(pos, config_version=5.2))


def test_a_composite_trade_still_has_the_thesis_exit(tmp_path):
    from test_policy_v4 import v4
    from test_paper_broker_open import ist, sig
    b, clock = v4(tmp_path)
    b.on_signal(sig(target=108.0, scorer=None))
    ev = None
    for minute in range(0, 12):
        clock.t = ist(10, 1 + minute)
        ev = b.on_score("SAIL", -0.3, ltp=100.4) or ev
    assert ev and ev["reason"] == "THESIS_EXIT"


def test_policy_v7_1_only_widens_the_alpha_stop():
    from core.policy_config import load_policy
    from paths import CONFIG_DIR
    v7, v71 = load_policy(CONFIG_DIR / "policy_v7.yaml"), load_policy(CONFIG_DIR / "policy_v7_1.yaml")
    assert v71.version == 7.1
    assert v71.conviction["alpha"] == dict(v7.conviction["alpha"], stop_atr=2.5)
    assert {k: v for k, v in v71.conviction.items() if k != "alpha"} ==         {k: v for k, v in v7.conviction.items() if k != "alpha"}
    assert (v71.horizon, v71.gates, v71.semantic, v71.regime) == (v7.horizon, v7.gates, v7.semantic, v7.regime)


def test_the_alpha_stop_follows_the_policy_multiple():
    from alpha.gate import apply
    out = apply(SETUP, PAYLOAD, "SAIL", dict(CFG, stop_atr=2.5), live=_live(0.95), tod=1.2)
    g = out["execution_geometry"]
    assert g["padded_stop"] == pytest.approx(100 - 2.5 * 0.8 * 1.2, abs=0.01)
    assert g["calculated_target"] == pytest.approx(100 + 1.5 * 2.5 * 0.8 * 1.2, abs=0.01)


def test_an_alpha_trade_keeps_its_target_but_a_composite_trade_is_pulled_in(tmp_path):
    """v5's pull-in to reach_atr x ATR15 would cut the 3.75-ATR alpha target that was tested."""
    from test_policy_v4 import v4
    from test_paper_broker_open import ist, sig
    for scorer, pulled in (("ALPHA", False), (None, True)):
        (tmp_path / str(scorer)).mkdir()
        b, clock = v4(tmp_path / str(scorer))
        b.reach_atr, b.reach_floor_r, b.reach_every_s = 3.0, 1.0, 0.0
        b.on_signal(sig(target=110.0, composite=0.9, scorer=scorer))
        clock.t = ist(10, 5)
        b.on_score("SAIL", 0.9, ltp=100.0, atr15=1.0)
        assert (b.position_for("SAIL")["target"] < 110.0) is pulled
