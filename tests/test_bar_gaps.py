"""Holes in the 5-minute bars after the feed was not running (2026-10-08 standby)."""
from datetime import date, datetime

import pandas as pd

from core.bar_gaps import (bars_from_minutes, fill, longest_run, minutes_from_upstox,
                           missing_today, session_slots)
from core.data_readiness import missing

DAY = date(2026, 10, 8)


def _bars(stamps):
    return pd.DataFrame({"timestamp": pd.to_datetime(stamps), "open": 1.0, "high": 1.0, "low": 1.0,
                         "close": 1.0, "volume": 10.0, "oi": 0.0})


def _minutes(start, n, price=100.0):
    ts = pd.date_range(start, periods=n, freq="1min")
    return pd.DataFrame({"ts": ts, "o": price, "h": price + 1, "l": price - 1, "c": price, "v": 5})


def test_session_slots_stop_before_the_live_bar():
    s = session_slots(DAY, datetime(2026, 10, 8, 9, 30))
    assert s == [datetime(2026, 10, 8, 9, 15), datetime(2026, 10, 8, 9, 20), datetime(2026, 10, 8, 9, 25)]
    assert len(session_slots(DAY)) == 75


def test_the_standby_hole_is_found():
    # yesterday's bars, then today's bars only from 13:10 (the 10-08 shape)
    df = _bars(["2026-10-07 14:45", "2026-10-08 13:10", "2026-10-08 13:15"])
    holes = missing_today(df, datetime(2026, 10, 8, 13, 20))
    assert holes[0] == datetime(2026, 10, 8, 9, 15) and holes[-1] == datetime(2026, 10, 8, 13, 5)
    assert longest_run(holes) == len(holes) == 47


def test_no_hole_when_every_bar_is_there():
    df = _bars(session_slots(DAY, datetime(2026, 10, 8, 10, 0)))
    assert missing_today(df, datetime(2026, 10, 8, 10, 0)) == []
    assert longest_run([]) == 0


def test_longest_run_counts_consecutive_slots_only():
    t = [datetime(2026, 10, 8, 9, 15), datetime(2026, 10, 8, 9, 30), datetime(2026, 10, 8, 9, 35)]
    assert longest_run(t) == 2


def test_minutes_become_five_minute_bars():
    b = bars_from_minutes(_minutes("2026-10-08 09:15", 10))
    assert list(b.timestamp) == list(pd.to_datetime(["2026-10-08 09:15", "2026-10-08 09:20"]))
    assert b.volume.tolist() == [25.0, 25.0] and b.high.tolist() == [101.0, 101.0]


def test_fill_adds_missing_bars_but_never_the_live_bar_or_existing_ones():
    df = _bars(["2026-10-08 09:15", "2026-10-08 09:30"])
    df.loc[1, "close"] = 7.0                                   # a live bar must not be replaced
    bars = bars_from_minutes(_minutes("2026-10-08 09:15", 25))  # 09:15 .. 09:35
    out, n = fill(df, bars, before=pd.Timestamp("2026-10-08 09:35"))
    assert n == 2                                              # 09:20 and 09:25
    assert list(out.timestamp.dt.strftime("%H:%M")) == ["09:15", "09:20", "09:25", "09:30"]
    assert out.loc[out.timestamp == "2026-10-08 09:30", "close"].item() == 7.0
    assert list(out.columns) == list(df.columns)


def test_upstox_candles_are_read_as_ist():
    m = minutes_from_upstox([["2026-10-08T13:40:00+05:30", 1, 2, 0.5, 1.5, 100, 0]])
    assert m.ts.iloc[0] == pd.Timestamp("2026-10-08 13:40") and m.v.iloc[0] == 100


def test_a_hole_blocks_new_entries_one_missing_bar_does_not():
    assert any("5-minute bars" in w for w in missing({"bars_gap_run": 47}))
    assert not any("5-minute bars" in w for w in missing({"bars_gap_run": 1}))
    assert not any("5-minute bars" in w for w in missing({}))


def _live_engine(today_stamps):
    from rolling_state_engine import RollingStateEngine
    now = pd.Timestamp.now().normalize()
    eng = RollingStateEngine.__new__(RollingStateEngine)
    stamps = [now - pd.Timedelta(days=1) + pd.Timedelta(hours=14, minutes=45)] + \
             [now + pd.Timedelta(hours=h, minutes=m) for h, m in today_stamps]
    eng.dfs = {"NSE_EQ|NBCC": {"ltf_df": _bars(stamps), "htf_df": pd.DataFrame()}}
    eng.phantom_candles = {"NSE_EQ|NBCC": {"timestamp": now + pd.Timedelta(hours=13, minutes=20),
                                           "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                                           "volume": 1.0, "oi": 0.0}}
    return eng, now


def test_the_feed_repairs_todays_hole_from_the_exchange(monkeypatch):
    import asyncio
    from data_services import upstox_feed
    eng, now = _live_engine([(13, 10), (13, 15)])
    assert len(eng.bar_holes("NSE_EQ|NBCC")) == 47
    start = now + pd.Timedelta(hours=9, minutes=15)
    candles = [[(start + pd.Timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:00+05:30"), 1, 2, 0.5, 1.5, 10, 0]
               for i in range(0, 250)]                         # 09:15 .. 13:24, incl. the live bar

    async def fake(state, token):
        return candles
    monkeypatch.setattr(upstox_feed, "_exchange_candles", fake)
    added = asyncio.run(upstox_feed.repair_bar_holes(None, eng))
    assert added == 47 and eng.bar_holes("NSE_EQ|NBCC") == []
    ts = eng.dfs["NSE_EQ|NBCC"]["ltf_df"].timestamp
    assert ts.is_monotonic_increasing and (ts < now + pd.Timedelta(hours=13, minutes=20)).all()


def test_the_candle_api_gets_the_isin_key_not_the_name(monkeypatch):
    from data_services import upstox_feed
    from historical_engine import HistoricalFetcher
    monkeypatch.setattr(HistoricalFetcher, "upstox_eq_map", {"NBCC": "NSE_EQ|INE095N01031"})
    assert upstox_feed.upstox_key("NSE_EQ|NBCC") == "NSE_EQ|INE095N01031"
    assert upstox_feed.upstox_key("NSE_EQ|INE095N01031") == "NSE_EQ|INE095N01031"
    assert upstox_feed.upstox_key("NSE_INDEX|Nifty 50") == "NSE_INDEX|Nifty 50"
