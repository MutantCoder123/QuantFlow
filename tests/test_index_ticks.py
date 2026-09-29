"""F1 (2026-09-29): the NIFTY 50 price never reached the app.

Upstox V3 sends an index tick as feeds[key].fullFeed.indexFF.ltpc (FullFeed is
a oneof of marketFF and indexFF). parse_tick read indexFF at the top level, so
every index tick parsed to ltp 0 and was dropped: zero NSE_INDEX ticks in the
day's recording, `ltp: None` in the state. The index now parses with its
previous close and day OHLC, and the rolling engine keeps five-minute index
bars that /api/bars serves like any stock's."""
from data_services.upstox_feed import build_bars_response, parse_tick
from rolling_state_engine import RollingStateEngine

INDEX_TICK = {"fullFeed": {"indexFF": {
    "ltpc": {"ltp": 24310.5, "ltt": "1790658000000", "cp": 24600.0},
    "marketOHLC": {"ohlc": [{"interval": "1d", "open": 24590.0, "high": 24660.0, "low": 24240.0, "close": 24310.5},
                            {"interval": "I1", "open": 24312.0, "high": 24315.0, "low": 24309.0, "close": 24310.5}]}}},
    "requestMode": "full_d5"}


def test_an_index_tick_nested_under_fullfeed_parses():
    t = parse_tick("NSE_INDEX|Nifty 50", INDEX_TICK, {})
    assert t is not None
    assert t.token == "NSE_INDEX|Nifty 50" and t.price == 24310.5
    assert t.close_price == 24600.0
    assert t.day_ohlc == {"open": 24590.0, "high": 24660.0, "low": 24240.0}
    assert t.bids == [] and t.volume == 0


def test_a_stock_tick_is_unchanged_and_carries_its_close_when_sent():
    feed = {"fullFeed": {"marketFF": {"ltpc": {"ltp": 132.5, "cp": 130.0}, "vtt": 10}}}
    t = parse_tick("NSE_EQ|X", feed, {"NSE_EQ|X": "NSE_EQ|SAIL"})
    assert t.token == "NSE_EQ|SAIL" and t.price == 132.5 and t.close_price == 130.0 and t.day_ohlc is None


def engine():
    eng = RollingStateEngine.__new__(RollingStateEngine)
    eng.dfs, eng.phantom_candles, eng.recorder = {}, {}, None
    eng.last_tick_ts, eng.index_bars = {}, {}
    return eng


def ms(h, m, s=0):
    """Epoch milliseconds for h:m:s IST on 29 Sep 2026 (midnight IST = 1790620200)."""
    return (1790620200 + h * 3600 + m * 60 + s) * 1000


def test_index_ticks_set_the_state_and_build_five_minute_bars(monkeypatch):
    from diagnostic_ui import TerminalDashboard
    monkeypatch.setattr(TerminalDashboard, "active_states", {"NSE_INDEX|Nifty 50": {"stock_pcr": 0.88}})
    eng = engine()
    key = "NSE_INDEX|Nifty 50"
    day = {"open": 24590.0, "high": 24660.0, "low": 24240.0}
    eng.process_tick(key, ms(9, 15, 5), 24590.0, 0, 0, close_price=24600.0, day_ohlc=day)
    eng.process_tick(key, ms(9, 17, 0), 24620.0, 0, 0, close_price=24600.0, day_ohlc=day)
    eng.process_tick(key, ms(9, 19, 59), 24580.0, 0, 0)
    eng.process_tick(key, ms(9, 20, 1), 24570.0, 0, 0)
    st = TerminalDashboard.active_states[key]
    assert st["ltp"] == 24570.0 and st["prev_close"] == 24600.0
    assert st["day_open"] == 24590.0 and st["day_high"] == 24660.0 and st["day_low"] == 24240.0
    assert st["stock_pcr"] == 0.88                         # what was there stays
    assert st["last_tick_ts"] > 0
    bars = eng.index_bars[key]
    assert [(b["open"], b["high"], b["low"], b["close"]) for b in bars] == [(24590.0, 24620.0, 24580.0, 24580.0),
                                                                           (24570.0, 24570.0, 24570.0, 24570.0)]
    assert str(bars[0]["timestamp"]) == "2026-09-29 09:15:00"


def test_the_bars_response_serves_index_bars_from_a_list():
    rows = [{"timestamp": "2026-09-29 09:15:00", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 0.0}]
    assert build_bars_response(rows, n=5)["bars"][0]["close"] == 1.5
