"""save_position_api must capture whale_cvd_at_entry so the A-6 polarity fix
(tests/test_gatekeeper_polarity.py) has a real baseline to compare against
instead of silently defaulting to 0.0 for every position.
"""
from fastapi.testclient import TestClient

import api_server
from reasoning_engine import ReasoningEngine

client = TestClient(api_server.app)


def setup_function():
    ReasoningEngine.user_positions.clear()
    api_server.local_active_states.clear()


def teardown_function():
    ReasoningEngine.user_positions.clear()
    api_server.local_active_states.clear()


def test_whale_cvd_at_entry_is_backfilled_from_live_state():
    api_server.local_active_states["NSE_EQ|SAIL"] = {"whale_cvd_ema_1h": -234_000.0}

    res = client.post("/api/reasoning/position/save", json={
        "symbol": "SAIL",
        "user_position": {"direction": "Long", "entry_price": 132.5},
    })
    assert res.status_code == 200

    stored = ReasoningEngine.user_positions["SAIL"]
    assert stored["whale_cvd_at_entry"] == -234_000.0


def test_explicit_whale_cvd_at_entry_is_not_overwritten():
    api_server.local_active_states["NSE_EQ|SAIL"] = {"whale_cvd_ema_1h": -234_000.0}

    client.post("/api/reasoning/position/save", json={
        "symbol": "SAIL",
        "user_position": {"direction": "Long", "entry_price": 132.5,
                          "whale_cvd_at_entry": 999.0},
    })

    assert ReasoningEngine.user_positions["SAIL"]["whale_cvd_at_entry"] == 999.0


def test_clearing_a_position_still_works():
    res = client.post("/api/reasoning/position/save", json={"symbol": "SAIL", "user_position": None})
    assert res.status_code == 200
    assert ReasoningEngine.user_positions["SAIL"] is None
