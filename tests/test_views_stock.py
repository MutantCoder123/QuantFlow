"""The Inspector's chart data: today's bars with a running VWAP, and the
levels drawn over them -- unknown levels stay None, never 0."""
import numpy as np
from fastapi.testclient import TestClient

import api_server
from views.stock import levels, session_bars


def bar(ts, o, h, l, c, v, **kw):
    return {"timestamp": ts, "open": o, "high": h, "low": l, "close": c, "volume": v, **kw}


def test_only_the_latest_session_oldest_first_with_a_running_vwap():
    raw = [bar("2026-09-29 09:20:00", 11, 12, 10, 11, 100), bar("2026-09-28 15:25:00", 50, 50, 50, 50, 900),
           bar("2026-09-29 09:15:00", 10, 11, 9, 10, 100)]
    out = session_bars(raw)
    assert out["session_date"] == "2026-09-29"
    assert [b["t"] for b in out["bars"]] == ["09:15", "09:20"]
    assert out["bars"][0]["vwap"] == 10.0                      # (11+9+10)/3
    assert out["bars"][1]["vwap"] == 10.5                      # (10*100 + 11*100) / 200
    assert out["bars"][0]["ts"] == 1790653500                  # 09:15 IST = 03:45 UTC
    assert out["bars"][0]["cvd"] is None


def test_bad_bars_are_dropped_and_no_volume_means_no_vwap():
    out = session_bars([bar("2026-09-29 09:15:00", None, 1, 1, 1, 0), bar("junk", 1, 1, 1, 1, 1),
                        bar("2026-09-29 09:20:00", 5, 6, 4, 5, 0, cvd=np.float64(-120.0))])
    assert len(out["bars"]) == 1
    assert out["bars"][0]["vwap"] is None and out["bars"][0]["cvd"] == -120.0
    assert session_bars([]) == {"session_date": None, "bars": []}


def test_levels_prefer_the_session_vwap_and_leave_unknowns_as_none():
    lv = levels({"ltp": np.float64(3262.2), "session_vwap": 0, "vwap_5m": 3258.1, "rolling_20d_poc_price": 3258,
                 "rolling_20d_value_area_high": "3270.6", "rolling_20d_value_area_low": None})
    assert lv["ltp"] == 3262.2 and lv["vwap"] == 3258.1 and lv["va_high"] == 3270.6
    assert lv["va_low"] is None and lv["max_pain"] is None and lv["prev_close"] is None


def test_the_endpoint_resolves_the_symbol_and_proxies_the_feed(monkeypatch):
    monkeypatch.setattr(api_server, "local_active_states", {"NSE_EQ|SAIL": {"ltp": 184.3, "session_vwap": 183.9}})
    seen = {}

    async def fake_get(port, endpoint, timeout=30):
        seen["url"] = (port, endpoint)
        return {"bars": [bar("2026-09-29 09:15:00", 184, 185, 183, 184.5, 1000)]}
    monkeypatch.setattr(api_server, "proxy_get", fake_get)
    client = TestClient(api_server.app)
    body = client.get("/api/stock/sail/bars").json()
    assert seen["url"] == (8001, "/api/bars?token=NSE_EQ%7CSAIL&n=90")
    assert body["status"] == "success" and body["symbol"] == "SAIL" and len(body["bars"]) == 1
    assert body["levels"]["vwap"] == 183.9
    assert client.get("/api/stock/NOPE/bars").json()["status"] == "error"
