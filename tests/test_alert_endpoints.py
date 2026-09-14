"""The UI polls three alert routes that did not exist on the server.

ReasoningEngine.global_alerts is faithfully populated on every actionable
verdict (capped at 50), but nothing ever exposed it. FastAPI's 404 failed
silently in the browser -- `data.count` was undefined, `undefined > 0` was
false, and the badge simply stayed hidden -- so the operator saw a
permanently empty alert tray and reasonably concluded there were no alerts.
"""
from fastapi.testclient import TestClient

import api_server
from reasoning_engine import ReasoningEngine

client = TestClient(api_server.app)


def setup_function():
    ReasoningEngine.global_alerts.clear()
    ReasoningEngine.global_alerts.extend([
        {"id": 2, "timestamp": 1, "symbol": "SAIL", "verdict": "CONFIRM",
         "action": "EXECUTE_LONG", "rationale": "x", "read": False},
        {"id": 1, "timestamp": 0, "symbol": "INFY", "verdict": "ABORT",
         "action": "PASS", "rationale": "y", "read": True},
    ])


def teardown_function():
    ReasoningEngine.global_alerts.clear()


def test_unread_count():
    res = client.get("/api/alerts/unread")
    assert res.status_code == 200
    assert res.json()["count"] == 1


def test_history_returns_all():
    body = client.get("/api/alerts/history").json()
    assert body["status"] == "success"
    assert len(body["alerts"]) == 2


def test_mark_read_flips_the_flag():
    assert client.post("/api/alerts/mark-read/2").json()["status"] == "success"
    assert client.get("/api/alerts/unread").json()["count"] == 0


def test_mark_read_on_unknown_id_is_a_noop_not_an_error():
    res = client.post("/api/alerts/mark-read/999")
    assert res.status_code == 200
    assert res.json()["status"] == "success"


def test_map_option_tokens_route_is_removed():
    """That endpoint only ever existed against the dead smart_api_feed.py on
    port 8001 -- proxying to it always errored. Removed rather than fixed."""
    res = client.post("/api/map-option-tokens")
    assert res.status_code == 404
