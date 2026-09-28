"""/api/paper/{metrics,equity,breakdowns,diagnostics,export} over a temporary
event log -- and the reconciliation that ties them together."""
import io

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import api_server
from paper import runtime as paper_rt
from test_paper_broker_open import make, sig
from test_performance_metrics import NOW, book

client = TestClient(api_server.app)


@pytest.fixture
def broker(tmp_path, monkeypatch):
    b, clock, _ = make(tmp_path, t=NOW)
    for e in book():
        b.store.append(e)
    monkeypatch.setattr(paper_rt, "_broker", b)
    monkeypatch.setattr(api_server, "_paper_arms", lambda sc: [])
    return b


def test_metrics_endpoint(broker):
    body = client.get("/api/paper/metrics?range=all").json()
    assert body["status"] == "success" and body["range"]["session_days"] == 2
    h = body["headline"]
    assert h["net_pnl"]["value"] == pytest.approx(600)
    assert h["win_rate"] == {"value": None, "n": 4, "min_n": 20, "status": "insufficient"}
    assert body["risk_adjusted"]["sharpe"]["status"] == "insufficient"
    assert [d["day"] for d in body["daily"]] == ["2026-09-21", "2026-09-22"]


def test_ranges_and_bad_ranges(broker):
    today = client.get("/api/paper/metrics?range=today").json()
    assert today["headline"]["net_pnl"]["value"] == pytest.approx(100)
    custom = client.get("/api/paper/trades?range=custom&from=2026-09-21&to=2026-09-21").json()
    assert [t["pos_id"] for t in custom["trades"]] == ["P1", "P2"]
    bad = client.get("/api/paper/metrics?range=week").json()
    assert bad["status"] == "error" and "Unknown range" in bad["message"]


def test_equity_breakdowns_diagnostics(broker):
    eq = client.get("/api/paper/equity?range=all").json()
    assert [p["pnl"] for p in eq["realised"]] == pytest.approx([0, 1000, 500, 800, 600])
    one = client.get("/api/paper/breakdowns?range=all&by=direction").json()
    assert set(one["breakdowns"]) == {"direction"} and one["min_n"] == 10
    every = client.get("/api/paper/breakdowns?range=all").json()["breakdowns"]
    assert "exit_reason" in every and "hour" in every
    assert client.get("/api/paper/breakdowns?by=weather").json()["status"] == "error"
    d = client.get("/api/paper/diagnostics?range=all").json()
    assert d["status"] == "success" and d["costs"]["total"]["value"] == pytest.approx(380)


def test_export_csv_and_parquet(broker):
    r = client.get("/api/paper/export?range=all&fmt=csv")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    df = pd.read_csv(io.BytesIO(r.content))
    assert list(df["pos_id"]) == ["P1", "P2", "P3", "P4"]
    assert df["net"].sum() == pytest.approx(600)
    assert (df["cost_total"] - df[[c for c in df if c.startswith("cost_") and c != "cost_total"]]
            .sum(axis=1)).abs().max() < 1e-9
    assert set(df["config_version"]) == {1}
    pq = client.get("/api/paper/export?range=all&fmt=parquet&kind=daily")
    daily = pd.read_parquet(io.BytesIO(pq.content))
    assert daily["net"].sum() == pytest.approx(600)
    assert client.get("/api/paper/export?fmt=xlsx").json()["status"] == "error"


def test_metrics_work_without_the_decision_loop(tmp_path, monkeypatch):
    """Figures are read from the log, so they survive the engine being down."""
    monkeypatch.setattr(paper_rt, "_broker", None)
    body = client.get("/api/paper/metrics?range=all").json()
    assert body["status"] == "success"


def test_everything_reconciles(broker):
    """Net P&L = Σ trade net = Σ daily net = the curve's last point = the
    export's net column; costs itemise exactly."""
    m = client.get("/api/paper/metrics?range=all").json()
    eq = client.get("/api/paper/equity?range=all").json()
    trades = client.get("/api/paper/trades?range=all").json()["trades"]
    csv = pd.read_csv(io.BytesIO(client.get("/api/paper/export?range=all&fmt=csv").content))
    net = sum(t["net"] for t in trades)
    assert m["headline"]["net_pnl"]["value"] == pytest.approx(net, abs=1e-9)
    assert sum(d["net"] for d in m["daily"]) == pytest.approx(net, abs=1e-9)
    assert eq["realised"][-1]["pnl"] == pytest.approx(net, abs=1e-9)
    assert csv["net"].sum() == pytest.approx(net, abs=1e-6)
    assert m["headline"]["gross_pnl"]["value"] - m["headline"]["costs"]["value"] == pytest.approx(net)


def test_open_events_carry_the_config_version(tmp_path):
    b, _, _ = make(tmp_path)
    b.config_version = 7
    ev = b.on_signal(sig())
    assert ev["type"] == "OPEN" and ev["config_version"] == 7
