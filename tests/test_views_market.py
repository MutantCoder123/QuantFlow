"""The Market tab's shaping (views.market, views.signals) and its endpoints.
Unknown stays None; the proxy breadth says it is a proxy; nothing is invented."""
import json
from datetime import date

import numpy as np
from fastapi.testclient import TestClient

import api_server
from views import market as M
from views.signals import engine_state, parse_report

NOW = 1790661120.0          # Tue 29 Sep 2026 11:42 IST


def test_nifty_from_the_feed_with_its_day():
    st = {"ltp": 24310.0, "prev_close": 24600.0, "day_open": 24590.0, "day_high": 24660.0, "day_low": 24240.0,
          "last_tick_ts": NOW - 2, "stock_pcr": np.float64(0.8876)}
    n = M.nifty(st, {"date": "2026-09-28", "close": 23100.0}, NOW)
    assert n["source"] == "feed" and n["change"] == -290.0 and round(n["change_pct"], 2) == -1.18
    assert n["age_s"] == 2.0 and n["stale"] is False and n["pcr"] == 0.8876


def test_nifty_before_the_feed_streams_it_shows_the_last_close_and_no_price():
    n = M.nifty({"stock_pcr": 0.9}, {"date": "2026-09-28", "close": 23100.0}, NOW)
    assert n["source"] == "daily" and n["ltp"] is None and n["change_pct"] is None
    assert n["last_close"] == 23100.0 and n["prev_close"] == 23100.0 and n["stale"] is True
    # the feed's price with no close yet borrows yesterday's daily close
    assert M.nifty({"ltp": 23000.0, "last_tick_ts": NOW}, {"date": "2026-09-28", "close": 23100.0}, NOW)["change"] == -100.0
    # a daily row dated today is not a previous close
    assert M.nifty({"ltp": 23000.0}, {"date": "2026-09-29", "close": 23100.0}, NOW)["prev_close"] is None
    assert M.nifty(None, None, NOW)["source"] is None


def test_index_history_ranges():
    rows = [{"date": "2021-01-04", "close": 14000.0}, {"date": "2026-09-01", "close": 24000.0},
            {"date": "2026-09-25", "close": 23035.0}, {"date": "2026-09-28", "close": 23100.0}]
    h = M.index_history(rows, "1W", date(2026, 9, 29))
    assert [p["date"] for p in h["points"]] == ["2026-09-25", "2026-09-28"]
    assert round(h["change_pct"], 3) == round((23100 - 23035) / 23035 * 100, 3)
    assert len(M.index_history(rows, "5Y", date(2026, 9, 29))["points"]) == 3
    try:
        M.index_history(rows, "2D", date(2026, 9, 29))
        raise AssertionError("an unknown range must be refused")
    except ValueError as e:
        assert "1W" in str(e)


def test_breadth_counts_only_with_the_nse_ratio():
    fs = {"breadth": {"advances": 7, "declines": 43, "unchanged": 0}}
    assert M.breadth(fs, 0.16, "NIFTY_50") == {"ratio": 0.16, "source": "NIFTY_50", "advances": 7,
                                               "declines": 43, "unchanged": 0}
    proxy = M.breadth(fs, 0.8, "WATCHLIST_PROXY")
    assert proxy["advances"] is None and proxy["source"] == "WATCHLIST_PROXY"


def test_flows_latest_with_its_date_and_the_last_sessions():
    fs = {"fii_net": -5353.22, "dii_net": 5189.02, "date": "N/A",
          "flow_history": [{"date": f"2026-09-{d:02d}", "fii_net": -d, "dii_net": d} for d in range(10, 29)]}
    f = M.flows(fs)
    assert f["latest"] == {"date": "2026-09-28", "fii_net": -5353.22, "dii_net": 5189.02}
    assert len(f["history"]) == 10 and f["history"][-1]["date"] == "2026-09-28"
    assert M.flows(None)["latest"]["fii_net"] is None


STATES = {
    "NSE_EQ|SAIL": {"symbol": "SAIL", "ltp": 181.53, "prev_close": 185.0, "obi": 0.2},
    "NSE_EQ|NMDC": {"ltp": 70.0, "prev_close": 71.0, "obi": -0.1},
    "NSE_EQ|INFY": {"ltp": 1512.4, "prev_close": 1506.2, "obi": 0.33},
    "NSE_EQ|OFSS": {"ltp": 0, "prev_close": 9000, "obi": 0.1},
    "NSE_EQ|IDEA": {"ltp": 9.87, "prev_close": 10.09, "obi": None},
    "NSE_INDEX|Nifty Bank": {"ltp": 50000, "prev_close": 49000},
}


def test_stocks_sectors_and_the_quadrant():
    rows = M.stocks(STATES)
    assert [r["symbol"] for r in rows] == ["IDEA", "INFY", "NMDC", "OFSS", "SAIL"]      # no index
    assert next(r for r in rows if r["symbol"] == "OFSS")["change_pct"] is None
    clusters = {"SAIL": "PSU_METALS_INFRA", "NMDC": "PSU_METALS_INFRA", "INFY": "IT", "OFSS": "IT", "IDEA": "TELECOM"}
    sec = M.sectors(rows, clusters)
    # IT has one priced stock, so it joins Telecom's IDEA under Others, last
    assert [s["name"] for s in sec] == ["Metals and infra", "Others"]
    assert [t["symbol"] for t in sec[0]["tiles"]] == ["NMDC", "SAIL"]                     # strongest first
    assert [t["symbol"] for t in sec[1]["tiles"]] == ["INFY", "IDEA"]
    q = M.quadrant(rows)
    assert {p["symbol"] for p in q} == {"INFY", "NMDC", "SAIL"}
    assert next(p for p in q if p["symbol"] == "SAIL")["absorbing"] is True
    assert next(p for p in q if p["symbol"] == "NMDC")["absorbing"] is False


def test_the_wire_takes_the_newest_headline_per_watchlist_stock():
    cache = {"SAIL": {"raw_news": [{"headline": "Wins order", "summary": "Rail"}, {"headline": "Older"}]},
             "NIFTY": {"raw_news": [{"headline": "Index news"}]}, "IDEA": {"raw_news": []}}
    assert M.wire(cache, {"SAIL", "IDEA"}) == [{"symbol": "SAIL", "headline": "Wins order", "summary": "Rail"}]


def test_sparks_keep_only_the_latest_session():
    bars = {"SAIL": [{"timestamp": "2026-09-28 15:25:00", "close": 180.0},
                     {"timestamp": "2026-09-29 09:15:00", "close": 184.0},
                     {"timestamp": "2026-09-29 09:20:00", "close": 0}], "IDEA": []}
    assert M.sparks(bars) == {"SAIL": [184.0], "IDEA": []}


def test_engine_state_in_words():
    assert engine_state(None, {"side": "SHORT", "r_now": 0.314}) == {"text": "Paper short · +0.31 R", "tone": "up", "rank": 0}
    assert engine_state(None, None, {"direction": "Short"})["text"] == "You’re short"
    rep = parse_report('```json\n{"Status_Tag": "LLM_ANALYZED", "Action": "Long"}\n```')
    assert engine_state(rep) == {"text": "AI says long", "tone": "up", "rank": 1}
    assert engine_state({"Status_Tag": "PENDING_LLM"})["text"] == "AI reviewing"
    assert engine_state({"Risk_Rejection": "CLUSTER_LIMIT_ADANI"})["tone"] == "down"
    assert engine_state({"Risk_Rejection": "NO_GEOMETRY", "Status_Tag": "RANK_GATED"})["text"] == "Below the top-5 cut"
    assert engine_state({"math_rejection": "NEUTRAL_CONVICTION"}) is None
    assert parse_report("Error generating report: x") is None


def test_the_summary_endpoint_assembles_it(monkeypatch):
    monkeypatch.setattr(api_server, "local_active_states", dict(STATES, **{"NSE_INDEX|Nifty 50": {"stock_pcr": 0.9}}))
    monkeypatch.setattr(api_server, "local_fii_dii_state", {"fii_net": -1.0, "dii_net": 2.0, "date": "2026-09-28"})
    monkeypatch.setattr(api_server, "local_macro_context", {"sentiment": "MIXED", "summary": "Flat.", "timestamp": 1})
    monkeypatch.setattr(api_server, "local_catalyst_cache", {})
    monkeypatch.setattr(api_server, "local_news_state", {"is_active": True, "interval": 120, "last_fetch_time": 5})
    monkeypatch.setattr(api_server, "_nifty_daily", lambda: [{"date": "2026-09-28", "close": 23100.0}])
    body = TestClient(api_server.app).get("/api/market/summary").json()
    assert body["status"] == "success"
    assert body["nifty"]["source"] == "daily" and body["nifty"]["pcr"] == 0.9
    assert body["breadth"]["source"] == "WATCHLIST_PROXY"
    assert body["flows"]["latest"]["date"] == "2026-09-28"
    assert body["context"]["sentiment"] == "MIXED"
    assert body["news"] == {"active": True, "interval": 120.0, "last_fetch": 5.0, "model": None, "reachable": True}
    assert body["symbols"] == ["IDEA", "INFY", "NMDC", "OFSS", "SAIL"]
    json.dumps(body)                                                # plain JSON all the way down
    # the real clusters.yaml groups SAIL and NMDC (not every stock under Others)
    assert body["sectors"][0]["name"] == "Metals and infra"


def test_index_history_endpoint(monkeypatch):
    monkeypatch.setattr(api_server, "_nifty_daily", lambda: [{"date": "2026-09-28", "close": 23100.0}])
    c = TestClient(api_server.app)
    assert c.get("/api/market/index-history?range=1W").json()["last"] == 23100.0
    assert c.get("/api/market/index-history?range=9Y").json()["status"] == "error"
