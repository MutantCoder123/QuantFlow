"""Policy v6: stop floor (time-of-day widened), target floor, expected-value gate,
regime gate, concentration and the value-cap rejection -- each behind its key."""
from datetime import datetime

import pytest

from core import tod_vol
from core.ev_calibration import expected_r

SLOTS = tod_vol.SLOTS


# ------------------------------------------------------------------ time of day
def test_the_factor_widens_a_busy_slot_and_never_narrows():
    r = {s: 1.0 for s in SLOTS}
    r["09:45"] = 1.5
    assert tod_vol.factor("X", datetime(2026, 10, 7, 9, 50), r) == pytest.approx(1.5, abs=0.01)
    r["12:00"] = 0.5                                                  # a quiet slot: no narrowing
    assert tod_vol.factor("X", datetime(2026, 10, 7, 12, 5), r) == 1.0
    r["10:00"] = 9.0
    assert tod_vol.factor("X", datetime(2026, 10, 7, 10, 1), r) == tod_vol.MAX_FACTOR
    assert tod_vol.factor("X", datetime(2026, 10, 7, 10, 1), {}) == 1.0


# ------------------------------------------------------------------ expected value
TABLE = {"regimes": {"TREND_EXPANSION": [[0.15, 0.25, 0.13, 154], [0.25, 0.35, 0.15, 30]],
                     "RANGE_BOUND_CHOP": [[0.15, 0.25, -0.22, 1213]]}}
CFG = {"min_ev_r": 0.05, "min_n": 50}


def test_expected_value_by_regime_and_band_or_none_when_thin():
    assert expected_r("TREND_EXPANSION", -0.2, CFG, TABLE) == (0.13, 154)
    assert expected_r("TREND_EXPANSION", 0.3, CFG, TABLE) == (None, 30)        # 30 < min_n
    assert expected_r("RANGE_BOUND_CHOP", 0.2, CFG, TABLE) == (-0.22, 1213)
    assert expected_r("PRE_BREAKOUT_SQUEEZE", 0.2, CFG, TABLE) == (None, 0)


# ------------------------------------------------------------------ the scorer's geometry
def test_a_noise_tight_stop_is_widened_and_the_target_follows():
    from conviction_scorer import widen_geometry
    # v5: a level 0.3 under the entry -> stop 0.3 away = 0.6 ATR15 (ATR15 0.5)
    stop, tgt = widen_geometry(100.0, 99.7, 101.0, "LONG", atr_eff=0.5 * 1.25, floor_k=2.0, target_min_r=1.5)
    assert stop == pytest.approx(100.0 - 2.0 * 0.625) and tgt == pytest.approx(100.0 + 1.5 * 1.25)
    stop, tgt = widen_geometry(100.0, 100.3, 99.0, "SHORT", atr_eff=0.5, floor_k=2.0, target_min_r=1.5)
    assert stop == pytest.approx(101.0) and tgt == pytest.approx(98.5)


def test_a_wider_structure_stop_and_a_farther_target_are_kept_and_v5_is_unchanged():
    from conviction_scorer import widen_geometry
    assert widen_geometry(100.0, 97.0, 106.0, "LONG", 0.5, 2.0, 1.5) == (97.0, 106.0)
    assert widen_geometry(100.0, 99.7, 101.0, "LONG", 0.5, 0.0, 0.0) == (99.7, 101.0)


# ------------------------------------------------------------------ gatekeeper regime gate
def test_the_regime_gate_blocks_new_trades_outside_the_list(monkeypatch):
    from intraday_gatekeeper import IntradayGatekeeper
    from core.policy_config import load_policy
    from paths import CONFIG_DIR
    pol = load_policy(CONFIG_DIR / "policy_v6.yaml")
    monkeypatch.setattr("core.policy_config.load_policy", lambda *a, **k: pol)
    monkeypatch.setattr("core.data_readiness.missing", lambda p: [])
    structured = {"market_regime": {"current_regime": "RANGE_BOUND_CHOP", "session_phase": "MORNING_SESSION"},
                  "math_setup": {"setup_rejected": False, "composite_score": 0.6, "directional_bias": "LONG",
                                 "execution_geometry": {}, "expectancy_matrix": {}}}
    with __import__("freezegun").freeze_time("2026-10-07 05:30:00"):          # 11:00 IST
        res = IntradayGatekeeper.evaluate(structured, {"ltp": 100.0, "market_state": "LIVE"}, {}, 100.0)
    assert res.get("math_rejection") == "REGIME_NOT_TRADED_RANGE_BOUND_CHOP"
    assert not res.get("llm_authorized")


# ------------------------------------------------------------------ broker: concentration, value cap
@pytest.fixture
def broker(tmp_path):
    from paper.broker import PaperBroker
    from paper.store import EventStore
    from core.costs import load_costs

    class Settings:
        def options(self):
            return {"enabled": True, "stale_price_seconds": 90}

        def effective(self):
            return {"capital": 1_000_000.0, "risk_per_trade_pct": 0.5, "max_daily_loss_pct": 2.0,
                    "max_cluster_risk_pct": 50.0, "max_adv_participation": 1.0, "max_position_value_x": 1.0,
                    "max_open_value_x": 50.0, "min_trade_risk_frac": 0.1, "slippage_pct": 0.0}

        def risk_limits(self):
            from core.risk import RiskLimits
            return RiskLimits(**{k: v for k, v in self.effective().items() if k != "slippage_pct"})

    class Clock:
        t = datetime(2026, 10, 7, 11, 0).timestamp() - 5.5 * 3600 + 5.5 * 3600

    clock = Clock()
    from zoneinfo import ZoneInfo
    clock.t = datetime(2026, 10, 7, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata")).timestamp()
    b = PaperBroker(store=EventStore(tmp_path), settings=Settings(), costs_cfg=load_costs(), clock=lambda: clock.t,
                    entry_cutoff="13:40", square_off="15:10", max_per_side=2, side_spacing_min=10,
                    reject_value_capped=True)
    return b, clock


def sig(sym, side="LONG", ltp=100.0, stop_d=2.0):
    stop = ltp - stop_d if side == "LONG" else ltp + stop_d
    tgt = ltp + 1.5 * stop_d if side == "LONG" else ltp - 1.5 * stop_d
    return {"symbol": sym, "action": f"EXECUTE_{side}", "ltp": ltp, "stop": stop, "target": tgt,
            "data_age_s": 0, "adv_shares": 1e9, "composite": 0.5, "regime": "TREND_EXPANSION", "verdict": "CONFIRM"}


def test_same_side_entries_are_spaced_and_capped(broker):
    b, clock = broker
    assert b.on_signal(sig("A"))["type"] == "OPEN"
    assert b.on_signal(sig("B"))["reason"] == "SIDE_SPACING"                 # 0 min after A
    assert b.on_signal(sig("C", "SHORT"))["type"] == "OPEN"                  # the other side is free
    clock.t += 11 * 60
    assert b.on_signal(sig("B"))["type"] == "OPEN"
    clock.t += 11 * 60
    assert b.on_signal(sig("D"))["reason"] == "SIDE_LIMIT"                   # 2 longs open already


def test_a_size_cut_by_the_value_cap_is_rejected_as_too_tight(broker):
    b, _ = broker
    # risk 5,000 / stop 0.20 = 25,000 shares = 25 L of stock > the 10 L cap
    assert b.on_signal(sig("T", stop_d=0.2))["reason"] == "STOP_TOO_TIGHT_FOR_SIZE"
    assert b.on_signal(sig("U", stop_d=2.0))["type"] == "OPEN"               # 2,500 shares = 2.5 L: fine


# ------------------------------------------------------------------ feature log: the score's parts
def test_the_score_parts_are_logged():
    from reasoning_engine import _component_features
    ms = {"contributions": {"micro": {"normalized": 0.4}, "struct": {"normalized": -0.2}, "cat": {"normalized": None}}}
    assert _component_features(ms) == {"comp_micro": 0.4, "comp_struct": -0.2}


def test_v6_policy_loads_and_v5_stays_the_default():
    from core.policy_config import DEFAULT_POLICY, load_policy
    from paths import CONFIG_DIR
    p = load_policy(CONFIG_DIR / "policy_v6.yaml")
    assert p.version == 6 and p.conviction["stop_floor_atr15"] == 2.0
    assert p.gates["entry_regimes"] == ["TREND_EXPANSION", "PRE_BREAKOUT_SQUEEZE"]
    assert DEFAULT_POLICY == "policy_v8_1.yaml"          # v6 failed its walk-forward: not live
