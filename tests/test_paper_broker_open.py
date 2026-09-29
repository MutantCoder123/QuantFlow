"""PaperBroker, opening side: a confirmed signal becomes a sized, persisted,
mirrored paper position -- or a REJECT event with a reason, never silence."""
from datetime import datetime

import numpy as np
import pytest
import yaml

from paper.broker import PaperBroker
from paper.settings import PaperSettings
from paper.store import EventStore

COSTS = {"brokerage_pct": 0.03, "brokerage_cap": 20, "stt_sell_pct": 0.025,
         "exchange_txn_pct": 0.00297, "sebi_pct": 0.0001, "stamp_duty_buy_pct": 0.003,
         "gst_pct": 18}


def ist(h, m, day=28):
    return datetime(2026, 9, day, h, m).timestamp()


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class Mirror(dict):
    """Stands in for ReasoningEngine.user_positions."""


def make(tmp_path, t=ist(10, 0), overrides=None, enabled=True):
    (tmp_path / "risk.yaml").write_text(yaml.safe_dump({
        "capital": 1_000_000, "risk_per_trade_pct": 0.5, "max_daily_loss_pct": 2.0,
        "max_cluster_risk_pct": 1.0, "max_adv_participation": 0.02}))
    (tmp_path / "paper.yaml").write_text(yaml.safe_dump({
        "enabled": enabled, "fill": {"slippage_pct": 0.0}, "stale_price_seconds": 15}))
    if overrides:
        (tmp_path / "ov.yaml").write_text(yaml.safe_dump(overrides))
    settings = PaperSettings(tmp_path / "risk.yaml", tmp_path / "paper.yaml", tmp_path / "ov.yaml")
    clock, mirror = Clock(t), Mirror()
    # Real config/clusters.yaml: SAIL and NMDC are in PSU_METALS_INFRA; IDEA is unmapped.
    b = PaperBroker(store=EventStore(tmp_path / "events"), settings=settings, costs_cfg=COSTS,
                    clock=clock, mirror=mirror, entry_cutoff="13:45", square_off="15:20")
    return b, clock, mirror


def sig(**kw):
    s = {"symbol": "SAIL", "token": "2963", "action": "EXECUTE_LONG", "verdict": "CONFIRM",
         "ltp": 100.0, "stop": 98.0, "target": 104.0, "data_age_s": 1.0,
         "adv_shares": 5_000_000, "whale_cvd": 0.0, "composite": 0.62,
         "regime": "TREND_EXPANSION", "session_phase": "MORNING_SESSION",
         "signal_id": "SIG-SAIL-1", "rationale": "trend + absorption",
         "manual_position": False}
    s.update(kw)
    return s


def test_a_confirmed_long_opens_sized_persisted_and_mirrored(tmp_path):
    b, _, mirror = make(tmp_path)
    ev = b.on_signal(sig())
    assert ev["type"] == "OPEN"
    # 0.5% of 10L = 5,000 risk / (100 - 98) = 2,500 shares; ADV cap 2% of 5M = 100k (not binding);
    # PSU_METALS_INFRA cluster budget 1% = 10,000 → 5,000 shares (not binding).
    assert ev["qty"] == 2500 and ev["risk_amount"] == pytest.approx(5000.0)
    assert ev["side"] == "LONG" and ev["entry_price"] == 100.0
    assert ev["cluster"] == "PSU_METALS_INFRA"
    assert ev["settings"]["capital"] == 1_000_000             # stamped
    assert ev["context"]["regime"] == "TREND_EXPANSION"
    m = mirror["SAIL"]
    assert m["source"] == "paper" and m["direction"] == "Long"
    assert m["entry_price"] == 100.0 and m["stoploss"] == 98.0
    assert m["entry_timestamp"] == ist(10, 0)
    assert len(b.store.load()) == 1


def test_a_short_mirrors_as_Short(tmp_path):
    b, _, mirror = make(tmp_path)
    ev = b.on_signal(sig(action="EXECUTE_SHORT", stop=102.0, target=96.0))
    assert ev["type"] == "OPEN" and ev["side"] == "SHORT"
    assert mirror["SAIL"]["direction"] == "Short"


def test_numpy_signal_values_are_handled(tmp_path):
    b, _, _ = make(tmp_path)
    ev = b.on_signal(sig(ltp=np.float64(100.0), stop=np.float64(98.0), target=np.float64(104.0),
                         adv_shares=np.float64(5e6), composite=np.float32(0.62)))
    assert ev["type"] == "OPEN" and type(ev["qty"]) is int


@pytest.mark.parametrize("kw, reason", [
    ({"action": "WAIT"}, "NOT_AN_ENTRY"),
    ({"stop": 0.0}, "NO_GEOMETRY"),
    ({"target": 0.0}, "NO_GEOMETRY"),
    ({"stop": 101.0}, "PRICE_BEYOND_GEOMETRY"),             # long with stop above price
    ({"target": 99.0}, "PRICE_BEYOND_GEOMETRY"),
    ({"data_age_s": 40.0}, "NO_FRESH_PRICE"),
    ({"ltp": 0.0}, "NO_FRESH_PRICE"),
    ({"adv_shares": 0.0}, "NO_ADV"),
    ({"manual_position": True}, "MANUAL_POSITION_HELD"),
])
def test_rejections_are_logged_with_a_reason(tmp_path, kw, reason):
    b, _, mirror = make(tmp_path)
    ev = b.on_signal(sig(**kw))
    assert ev["type"] == "REJECT" and ev["reason"] == reason
    assert "SAIL" not in mirror
    assert b.store.load()[-1]["reason"] == reason


def test_after_the_entry_cutoff_is_rejected(tmp_path):
    b, _, _ = make(tmp_path, t=ist(13, 46))
    assert b.on_signal(sig())["reason"] == "AFTER_ENTRY_CUTOFF"


def test_one_position_per_symbol(tmp_path):
    b, _, _ = make(tmp_path)
    b.on_signal(sig())
    assert b.on_signal(sig(signal_id="SIG-SAIL-2"))["reason"] == "ALREADY_OPEN"


def test_paused_engine_rejects_new_entries(tmp_path):
    b, _, _ = make(tmp_path)
    b.pause()
    assert b.on_signal(sig())["reason"] == "ENGINE_PAUSED"
    b.resume()
    assert b.on_signal(sig())["type"] == "OPEN"


def test_cluster_budget_counts_open_paper_risk(tmp_path):
    b, _, _ = make(tmp_path)
    b.on_signal(sig(signal_id="S1"))                               # 5,000 risk in PSU_METALS_INFRA
    ev = b.on_signal(sig(symbol="NMDC", signal_id="S2"))           # 5,000 headroom left
    assert ev["type"] == "OPEN" and ev["risk_amount"] == pytest.approx(5000.0)
    ev = b.on_signal(sig(symbol="NMDC", signal_id="S3"))
    assert ev["reason"] == "ALREADY_OPEN"


def test_cluster_budget_exhausted_rejects(tmp_path):
    b, _, _ = make(tmp_path, overrides={"risk_per_trade_pct": 1.0})   # 10,000 = whole PSU_METALS_INFRA budget
    assert b.on_signal(sig(signal_id="S1"))["type"] == "OPEN"
    assert b.on_signal(sig(symbol="NMDC", signal_id="S2"))["reason"] == "CLUSTER_LIMIT_PSU_METALS_INFRA"


def test_settings_changes_apply_to_new_positions_only(tmp_path):
    b, _, _ = make(tmp_path)
    first = b.on_signal(sig(signal_id="S1"))
    ok, _ = b.update_settings({"capital": 500_000})
    assert ok
    second = b.on_signal(sig(symbol="IDEA", signal_id="S2", ltp=10.0, stop=9.8, target=10.4))
    assert first["settings"]["capital"] == 1_000_000 and first["qty"] == 2500
    assert second["settings"]["capital"] == 500_000
    assert second["risk_amount"] <= 500_000 * 0.005 + 1e-6
    changes = [e for e in b.store.load() if e["type"] == "SETTINGS"]
    assert changes and changes[0]["new"] == {"capital": 500_000}


def test_invalid_settings_write_no_event(tmp_path):
    b, _, _ = make(tmp_path)
    ok, errors = b.update_settings({"capital": -1})
    assert not ok and "capital" in errors
    assert not [e for e in b.store.load() if e["type"] == "SETTINGS"]


def test_a_persistence_failure_stops_new_entries_and_is_reported(tmp_path):
    b, _, mirror = make(tmp_path)

    def boom(e):
        raise OSError("disk full")
    b.store.append = boom
    assert b.on_signal(sig()) is None
    assert b.engine_ok is False and "disk full" in b.last_error
    assert "SAIL" not in mirror                 # never trade on unpersisted state


def test_the_broker_counts_open_positions_toward_the_value_limit(tmp_path):
    """Each OPEN's qty x entry goes into the book the sizer sees."""
    import yaml
    b, clock, _ = make(tmp_path)
    first = b.on_signal(sig())
    assert first["type"] == "OPEN"
    held = first["qty"] * first["entry_price"]
    risk = yaml.safe_load(b.settings.risk_path.read_text())
    risk["max_open_value_x"] = held * 0.99 / risk["capital"]     # the open position already exceeds it
    b.settings.risk_path.write_text(yaml.safe_dump(risk))
    second = b.on_signal(sig(symbol="BHEL", token="438"))
    assert second["type"] == "REJECT" and second["reason"] == "OPEN_VALUE_LIMIT"
