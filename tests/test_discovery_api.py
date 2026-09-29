"""API-process side of Discovery.

watchlist.csv now has a writer in each process: the feed process (a
Discovery run) and this one (manual edits). This process used to load the
file once at import and rebuild it from that copy on every edit -- so the
first manual add after a Discovery run silently wrote the pre-Discovery
list back over it. It now reads the file each time.

The old playbook path is gone too: it rendered a months-old LLM playbook,
with invented entry/target/stoploss prices, on every startup.
"""
import csv

import pytest
from fastapi.testclient import TestClient

import api_server
from diagnostic_ui import TerminalDashboard

client = TestClient(api_server.app)


def _write(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Token", "Symbol", "Exchange"])
        for tok, sym in rows:
            w.writerow([tok, sym, "NSE"])


def _read(path):
    with open(path, encoding="utf-8") as f:
        return [(r["Token"], r["Symbol"]) for r in csv.DictReader(f)]


@pytest.fixture
def wl(tmp_path, monkeypatch):
    path = tmp_path / "watchlist.csv"
    monkeypatch.setattr(api_server, "watchlist_path", str(path))
    proxied = []

    async def fake_post(port, endpoint, payload=None, timeout=300):
        proxied.append((port, endpoint, payload))
        return {"status": "success"}

    async def fake_get(port, endpoint, timeout=30):
        proxied.append((port, endpoint, None))
        return {"status": "success", "job": {"state": "idle"}}

    async def no_sync(symbols):
        return None

    monkeypatch.setattr(api_server, "proxy_post", fake_post)
    monkeypatch.setattr(api_server, "proxy_get", fake_get)
    import data_services.parquet_engine as pe
    monkeypatch.setattr(pe, "sync_eod_parquet", no_sync)
    return path, proxied


def test_get_watchlist_reflects_a_write_from_the_feed_process(wl):
    path, _ = wl
    _write(path, [("1", "SAIL")])
    assert [r["symbol"] for r in client.get("/api/watchlist").json()["data"]] == ["SAIL"]
    _write(path, [("2", "BHEL")])                    # a Discovery run, elsewhere
    assert [r["symbol"] for r in client.get("/api/watchlist").json()["data"]] == ["BHEL"]


def test_manual_add_after_discovery_does_not_revert_it(wl):
    path, _ = wl
    _write(path, [("1", "SAIL"), ("9", "IDEA")])
    client.get("/api/watchlist")                     # this process has seen A
    _write(path, [("1", "SAIL"), ("2", "BHEL")])     # Discovery: IDEA out, BHEL in
    r = client.post("/api/watchlist/add",
                    json={"token": "3", "symbol": "INFY", "exchange": "NSE"})
    assert r.json()["status"] == "success"
    assert _read(path) == [("1", "SAIL"), ("2", "BHEL"), ("3", "INFY")]


def test_discovery_routes_proxy_to_the_feed_process(wl):
    _, proxied = wl
    client.post("/api/discovery/run")
    client.post("/api/discovery/run", json={"model": "gemini-2.5-pro"})
    client.get("/api/discovery/status")
    assert (8001, "/api/discovery/run", {"model": "ollama:qwen2.5:7b"}) in proxied   # the local default
    assert (8001, "/api/discovery/run", {"model": "gemini-2.5-pro"}) in proxied
    assert (8001, "/api/discovery/status", None) in proxied


def test_the_llm_playbook_route_is_gone():
    assert client.post("/api/reasoning/playbook/generate", json={}).status_code in (404, 405)
    assert client.post("/api/run-screener").status_code in (404, 405)


def test_no_persisted_playbook_is_carried_into_the_dashboard():
    assert not hasattr(TerminalDashboard, "dashboard_intraday_plays")
    import reasoning_engine
    assert not hasattr(reasoning_engine.ReasoningEngine, "generate_intraday_playbook")


def test_model_list_offers_installed_ollama_models_then_gemini(monkeypatch):
    async def tags():
        return ["qwen2.5:7b", "llama3.2:1b"]
    monkeypatch.setattr(api_server, "_ollama_models", tags)
    body = client.get("/api/ai/models").json()
    assert [m["id"] for m in body["models"]] == ["ollama:qwen2.5:7b", "ollama:llama3.2:1b", "gemini-2.5-flash"]
    assert {"id": "ollama:qwen2.5:7b", "label": "Local: qwen2.5:7b"} in body["models"]
    assert body["default"] == "ollama:qwen2.5:7b"


def test_model_list_still_works_when_ollama_is_down(monkeypatch):
    async def tags():
        raise OSError("connection refused")
    monkeypatch.setattr(api_server, "_ollama_models", tags)
    models = client.get("/api/ai/models").json()["models"]
    assert [m["id"] for m in models] == ["gemini-2.5-flash"]
