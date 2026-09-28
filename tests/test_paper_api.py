"""/api/paper/* and /api/legacy/trades over a temporary broker."""
import json

import pytest
from fastapi.testclient import TestClient

import api_server
from paper import runtime as paper_rt
from test_paper_broker_open import make, sig

client = TestClient(api_server.app)


@pytest.fixture
def broker(tmp_path, monkeypatch):
    b, clock, mirror = make(tmp_path)
    monkeypatch.setattr(paper_rt, "_broker", b)
    return b


def test_settings_show_effective_values_and_their_source(broker):
    body = client.get("/api/paper/settings").json()
    assert body["settings"]["capital"] == 1_000_000
    assert body["sources"]["capital"] == "risk.yaml"
    assert set(body["editable"]) >= {"capital", "slippage_pct", "risk_per_trade_pct"}


def test_saving_settings_validates_and_records_the_change(broker):
    r = client.post("/api/paper/settings", json={"capital": 600000}).json()
    assert r["status"] == "success" and r["settings"]["capital"] == 600000
    assert r["sources"]["capital"] == "override"
    bad = client.post("/api/paper/settings", json={"slippage_pct": 5}).json()
    assert bad["status"] == "error" and "slippage_pct" in bad["errors"]


def test_pause_and_resume(broker):
    assert client.post("/api/paper/disable").json()["enabled"] is False
    assert broker.on_signal(sig())["reason"] == "ENGINE_PAUSED"
    assert client.post("/api/paper/enable").json()["enabled"] is True


def test_positions_trades_and_rejections(broker):
    broker.on_signal(sig())
    broker.on_signal(sig(symbol="IDEA", signal_id="S2", data_age_s=99))
    pos = client.get("/api/paper/positions").json()
    assert pos["engine_ok"] is True and [p["symbol"] for p in pos["open"]] == ["SAIL"]
    broker.check_touches("SAIL", bars=None, ltp=104.2)
    trades = client.get("/api/paper/trades").json()["trades"]
    assert trades[0]["reason"] == "TARGET" and trades[0]["net"] is not None
    rej = client.get("/api/paper/rejections").json()["rejections"]
    assert rej[0]["reason"] == "NO_FRESH_PRICE"


def test_engine_not_started_is_reported_not_faked(monkeypatch):
    monkeypatch.setattr(paper_rt, "_broker", None)
    body = client.get("/api/paper/positions").json()
    assert body["status"] == "error" and body["engine_ok"] is False
    assert "open" not in body                      # no fake "0 open positions"


def test_legacy_trades_are_labelled_and_separate(tmp_path, monkeypatch):
    f = tmp_path / "trade_history.json"
    f.write_text(json.dumps({"T1": {"trade_id": "T1", "symbol": "SAIL", "status": "CLOSED",
                                    "realized_pnl": 60.0}}))
    monkeypatch.setattr(api_server, "TRADE_HISTORY_PATH", f)
    body = client.get("/api/legacy/trades").json()
    assert body["source"] == "legacy_mock_platform"
    assert "does not represent the current engine" in body["note"]
    assert body["trades"][0]["trade_id"] == "T1"
