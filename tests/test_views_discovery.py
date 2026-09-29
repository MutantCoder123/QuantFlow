"""The Discovery tab's shaping (views.discovery) and GET /api/discovery/view."""
from fastapi.testclient import TestClient

import api_server
from views.discovery import notes, view


def pick(sym, score, bias="LONG", token=None, **kw):
    return {"symbol": sym, "token": token or f"T{sym}", "exchange": "NSE", "score": score, "directional_bias": bias,
            "rs": 2.0, "adv_crore": 100.0, **kw}


DONE = {"state": "done", "started_at": "2026-09-29T08:50:00", "finished_at": "2026-09-29T08:52:10",
        "picks": [pick("TATAPOWER", 71, "SHORT", news="Guidance cut", news_source="fetched"), pick("MCX", 66)]
        + [pick(f"S{i}", 60 - i) for i in range(10)],
        "universe": [{"symbol": "TATAPOWER", "score": 71, "rs": -4.2, "adv_crore": 412, "directional_bias": "SHORT"},
                     {"symbol": "X", "score": None, "rs": 1.0, "adv_crore": 1, "directional_bias": "LONG"}],
        "watchlist_written": True, "live_added": ["TMCX"], "subscription_pending": [],
        "removals_deferred": [{"symbol": "IDEA"}],
        "analysis": {"model": "ollama:qwen2.5:7b", "device": "GPU",
                     "items": {"TATAPOWER": {"thesis": "Lagging.", "risks": ["Max pain above"], "watch_for": "352",
                                             "news_alignment": "SUPPORTS"}}}}


def test_the_view_ranks_signs_and_reads():
    v = view(DONE, {"MCX", "IDEA"})
    assert v["duration_s"] == 130 and v["state"] == "done"
    t, m = v["picks"][0], v["picks"][1]
    assert (t["rank"], t["side"], t["signed"], t["news_source"]) == (1, "Short", -0.71, "fetched")
    assert t["ai"] == {"state": "ok", "thesis": "Lagging.", "watch_for": "352", "risks": ["Max pain above"],
                       "news_alignment": "SUPPORTS"}
    assert m["ai"] == {"state": "missing"} and m["on_watchlist"] and m["added_live"]
    assert v["picks"][11]["ai"] is None                          # past the top 10: the AI never read it
    assert v["cut"] == 0.53                                      # the 10th pick (S7) scores 53
    assert v["universe"] == [{"symbol": "TATAPOWER", "rs": -4.2, "y": -0.71, "adv_crore": 412.0}]
    assert v["ai_model"] == "ollama:qwen2.5:7b"


def test_before_any_scan_and_while_reading():
    idle = view({"state": "idle", "picks": None}, set())
    assert idle["picks"] is None and idle["universe"] is None and idle["cut"] is None and idle["notes"] == []
    reading = view({"state": "running", "phase": "analyzing", "picks": [pick("A", 50)]}, set())
    assert reading["picks"][0]["ai"] == {"state": "reading"}


def test_notes_say_what_the_run_did():
    assert notes(DONE) == ["The watchlist was updated; 1 stock went live.",
                           "Leaving the watchlist at the next restart: IDEA."]
    assert notes({"state": "error", "error": "boom"}) == ["The scan failed: boom. The watchlist was not changed."]
    assert notes({"state": "done", "watchlist_written": False}) == ["No selection was made, so the watchlist is unchanged."]
    assert "GPU" in notes({"state": "done", "watchlist_written": False, "analysis": {"device": "CPU"}})[1]


def test_the_endpoint_says_when_the_feed_is_down(monkeypatch):
    async def down(port, endpoint, timeout=30):
        return {"status": "error", "message": "Service on 8001 unreachable"}
    monkeypatch.setattr(api_server, "proxy_get", down)
    body = TestClient(api_server.app).get("/api/discovery/view").json()
    assert body["status"] == "error" and body["feed_down"] is True

    async def up(port, endpoint, timeout=30):
        return {"status": "success", "job": DONE}
    monkeypatch.setattr(api_server, "proxy_get", up)
    monkeypatch.setattr(api_server, "_reload_watchlist", lambda: {"TMCX": {"symbol": "MCX-EQ", "exchange": "NSE"}})
    body = TestClient(api_server.app).get("/api/discovery/view").json()
    assert body["status"] == "success" and body["picks"][1]["on_watchlist"] is True
