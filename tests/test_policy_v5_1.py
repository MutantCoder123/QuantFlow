"""Policy v5.1: v5 with the stop moved out of the noise in the paper broker."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

IST = ZoneInfo("Asia/Kolkata")


class Settings:
    def options(self):
        return {"enabled": True, "stale_price_seconds": 90}

    def effective(self):
        return {"capital": 1_000_000.0, "risk_per_trade_pct": 0.5, "max_daily_loss_pct": 3.0,
                "max_cluster_risk_pct": 50.0, "max_adv_participation": 1.0, "max_position_value_x": 1.0,
                "max_open_value_x": 50.0, "min_trade_risk_frac": 0.1, "slippage_pct": 0.0}

    def risk_limits(self):
        from core.risk import RiskLimits
        return RiskLimits(**{k: v for k, v in self.effective().items() if k != "slippage_pct"})


def _broker(tmp_path, stop_floor):
    from core.costs import load_costs
    from paper.broker import PaperBroker
    from paper.store import EventStore
    t = datetime(2026, 10, 9, 11, 0, tzinfo=IST).timestamp()
    return PaperBroker(store=EventStore(tmp_path), settings=Settings(), costs_cfg=load_costs(), clock=lambda: t,
                       entry_cutoff="13:40", square_off="15:10", stop_floor=stop_floor)


def _sig(side="LONG", stop_d=0.4, atr=1.0, tgt_d=0.8):
    ltp = 100.0
    s = -1 if side == "LONG" else 1
    return {"symbol": "SAIL", "action": f"EXECUTE_{side}", "ltp": ltp, "stop": ltp + s * stop_d,
            "target": ltp - s * tgt_d, "data_age_s": 0, "adv_shares": 1e9, "composite": 0.4,
            "regime": "RANGE_BOUND_CHOP", "verdict": "CONFIRM", "atr15": atr}


FLOOR = {"atr15": 1.5, "tod": False, "target_min_r": 1.5}


def test_policy_v5_1_only_adds_the_stop_floor():
    from core.policy_config import load_policy
    from paths import CONFIG_DIR
    v5, v51 = load_policy(CONFIG_DIR / "policy_v5.yaml"), load_policy(CONFIG_DIR / "policy_v5_1.yaml")
    assert v51.version == 5.1 and v5.version == 5
    assert v51.horizon["stop_floor"] == {"atr15": 1.5, "tod": True, "target_min_r": 1.5}
    assert {k: v for k, v in v51.horizon.items() if k != "stop_floor"} == v5.horizon
    assert (v51.semantic, v51.regime, v51.conviction, v51.gates) == (v5.semantic, v5.regime, v5.conviction, v5.gates)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_a_noise_stop_is_widened_and_the_size_follows(tmp_path, side):
    b = _broker(tmp_path, FLOOR)
    ev = b.on_signal(_sig(side))
    s = 1 if side == "LONG" else -1
    assert ev["type"] == "OPEN"
    assert ev["stop"] == pytest.approx(100.0 - s * 1.5)             # 0.4 -> 1.5 x ATR15
    assert ev["target"] == pytest.approx(100.0 + s * 2.25)          # >= 1.5 x the new risk
    assert ev["qty"] == 3333 and ev["risk_amount"] == pytest.approx(4999.5, abs=1)   # 0.5% risk, not the value cap
    sf = ev["context"]["stop_floor"]
    assert sf["stop_was"] == pytest.approx(100.0 - s * 0.4) and sf["stop_atr_was"] == 0.4


def test_without_the_floor_the_same_signal_is_value_capped(tmp_path):
    ev = _broker(tmp_path, None).on_signal(_sig())
    assert ev["stop"] == pytest.approx(99.6) and ev["qty"] == 10000       # 1x capital cap, risk 4,000
    assert "stop_floor" not in ev["context"]


def test_a_wide_stop_and_a_far_target_are_kept(tmp_path):
    ev = _broker(tmp_path, FLOOR).on_signal(_sig(stop_d=2.0, tgt_d=5.0))
    assert ev["stop"] == pytest.approx(98.0) and ev["target"] == pytest.approx(105.0)
    assert "stop_floor" not in ev["context"]


def test_unknown_atr_leaves_the_stop_alone(tmp_path):
    ev = _broker(tmp_path, FLOOR).on_signal(_sig(atr=None))
    assert ev["stop"] == pytest.approx(99.6)


def test_time_of_day_widens_the_floor(tmp_path, monkeypatch):
    monkeypatch.setattr("core.tod_vol.factor", lambda sym, now, ratios=None: 1.4)
    ev = _broker(tmp_path, dict(FLOOR, tod=True)).on_signal(_sig())
    assert ev["stop"] == pytest.approx(100.0 - 1.5 * 1.4)
    assert ev["context"]["stop_floor"]["tod"] == 1.4


def test_runtime_hands_the_broker_the_policy_floor_and_the_atr():
    import inspect
    from paper import runtime
    src = inspect.getsource(runtime)
    assert 'stop_floor=horizon.get("stop_floor")' in src and '"atr15": raw.get("atr_15m")' in src


def test_policy_v5_2_only_tightens_the_run_on_trail():
    from core.policy_config import load_policy
    from paths import CONFIG_DIR
    v51, v52 = load_policy(CONFIG_DIR / "policy_v5_1.yaml"), load_policy(CONFIG_DIR / "policy_v5_2.yaml")
    assert v52.version == 5.2
    assert v52.horizon["run_on"] == {"trail_r": 0.5} and v51.horizon["run_on"] == {"trail_r": 1.0}
    assert {k: v for k, v in v52.horizon.items() if k != "run_on"} == {k: v for k, v in v51.horizon.items() if k != "run_on"}
    assert (v52.semantic, v52.regime, v52.conviction, v52.gates) == (v51.semantic, v51.regime, v51.conviction, v51.gates)
