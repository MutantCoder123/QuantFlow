import asyncio
import pytest
from data_services.upstox_feed import parse_tick


def test_parse_tick_extracts_book_and_volume():
    feed = {"fullFeed": {"marketFF": {"ltpc": {"ltp": 132.5}, "vtt": 1_000_000, "oi": 0,
                                       "marketLevel": {"bidAskQuote": [
                                           {"bq": 900, "bp": 132.45, "aq": 1100, "ap": 132.55}]}}}}
    t = parse_tick("NSE_EQ|INE114A01011", feed, {"NSE_EQ|INE114A01011": "NSE_EQ|SAIL"})
    assert t.token == "NSE_EQ|SAIL"
    assert t.price == 132.5 and t.volume == 1_000_000
    assert t.bids[0]["quantity"] == 900 and t.asks[0]["price"] == 132.55


def test_parse_tick_returns_none_without_price():
    assert parse_tick("X", {"fullFeed": {"marketFF": {"ltpc": {"ltp": 0}}}}, {}) is None


@pytest.mark.asyncio
async def test_ingest_loop_drains_the_queue():
    from rolling_state_engine import RollingStateEngine
    eng = RollingStateEngine.__new__(RollingStateEngine)
    eng.tick_q = asyncio.Queue()
    eng.dfs, eng.watchlist, eng.phantom_candles = {}, {}, {}
    eng._failures, eng.recorder = {}, None
    seen = []
    eng.process_tick = lambda **kw: seen.append(kw["token"])

    await eng.tick_q.put({"token": "A", "timestamp_ms": 1, "price": 1.0,
                          "volume": 1.0, "oi": 0.0, "greeks": None,
                          "bids": [], "asks": []})
    task = asyncio.create_task(eng.ingest_loop())
    await asyncio.sleep(0.05)
    task.cancel()
    assert seen == ["A"]


@pytest.mark.asyncio
async def test_queue_depth_reports_pending_backlog():
    from rolling_state_engine import RollingStateEngine
    eng = RollingStateEngine.__new__(RollingStateEngine)
    eng.tick_q = asyncio.Queue()
    for i in range(3):
        eng.tick_q.put_nowait({"token": str(i)})
    assert eng.queue_depth == 3
