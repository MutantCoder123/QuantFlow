"""A removed manual position stays removed: browsers resend their stored copy on
every load, and the server ignores it and tells the page to delete it."""
from fastapi.testclient import TestClient

import api_server
from reasoning_engine import ReasoningEngine

POS = {"mode": "Intraday", "direction": "Long", "quantity": 10, "entry_price": 100.0}


def test_removed_positions_are_not_restored_by_a_browser_sync(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "REMOVED_POSITIONS_PATH", tmp_path / "manual_removed.json")
    monkeypatch.setattr(ReasoningEngine, "user_positions", {"NBCC": POS, "SCI": POS})
    assert api_server.remove_manual_positions(["NBCC", "NSE_EQ|SCI"]) == ["NBCC", "SCI"]
    assert "NBCC" not in ReasoningEngine.user_positions and "SCI" not in ReasoningEngine.user_positions
    c = TestClient(api_server.app)
    r = c.post("/api/reasoning/position/sync_all", json={"positions": {"NBCC": POS, "SAIL": POS}}).json()
    assert r["drop"] == ["NBCC"]
    assert "NBCC" not in ReasoningEngine.user_positions and ReasoningEngine.user_positions["SAIL"] == POS
    # entered again on purpose: it counts again
    c.post("/api/reasoning/position/save", json={"symbol": "NBCC", "user_position": POS})
    assert c.post("/api/reasoning/position/sync_all", json={"positions": {"NBCC": POS}}).json()["drop"] == []
