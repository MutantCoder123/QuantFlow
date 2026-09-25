"""Applying a Discovery selection to the running feed.

Before this, a screener run rewrote watchlist.csv and the feed process --
which reads that file once at startup -- never noticed: the scan "worked"
and changed nothing until a restart. Additions now go live through the
same path a manual add uses. Removals are deferred to the next restart and
reported: unsubscribing mid-session would silently drop monitoring on a
stock that may be carrying an open position.
"""
from types import SimpleNamespace

import pytest

from data_services import upstox_feed


class _Stream:
    def __init__(self):
        self.subs = []

    def subscribe(self, keys, mode):
        self.subs.append((list(keys), mode))


class _Fetcher:
    def __init__(self):
        self.warmed = []

    async def fetch_batch_warmups(self, client, entries):
        self.warmed.append(dict(entries))
        return {tok: {"ltf_df": None} for tok in entries}


def _state(live: dict):
    stream = SimpleNamespace(stream_equity=_Stream(), stream_options=_Stream(),
                             reverse_map={})
    engine = SimpleNamespace(watchlist=dict(live), dfs={})
    return SimpleNamespace(watchlist=dict(live), rolling_engine=engine,
                           stream_manager=stream, fetcher=_Fetcher(),
                           upstox_api_client=object())


def _row(sym, tok):
    """A watchlist row as start_upstox_service loads it from watchlist.csv."""
    return {"Token": tok, "Symbol": sym, "Exchange": "NSE"}


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    import scrip_master_engine
    monkeypatch.setattr(scrip_master_engine, "get_instrument_key",
                        lambda sym: f"NSE_EQ|ISIN-{sym}")


async def test_new_symbols_are_warmed_registered_and_subscribed():
    st = _state({"1": _row("SAIL", "1")})
    selected = [{"token": "1", "symbol": "SAIL", "exchange": "NSE"},
                {"token": "2", "symbol": "BHEL", "exchange": "NSE"}]
    added, deferred = await upstox_feed.apply_discovery_selection(st, selected)

    assert added == ["2"]
    assert deferred == []
    assert "2" in st.watchlist and "2" in st.rolling_engine.watchlist
    assert "2" in st.rolling_engine.dfs
    assert st.stream_manager.stream_equity.subs == [(["NSE_EQ|ISIN-BHEL"], "full_d30")]
    assert st.stream_manager.reverse_map["NSE_EQ|ISIN-BHEL"] == "NSE_EQ|BHEL"


async def test_dropped_symbols_stay_live_and_are_reported_as_deferred():
    st = _state({"1": _row("SAIL", "1"), "9": _row("IDEA", "9")})
    selected = [{"token": "1", "symbol": "SAIL", "exchange": "NSE"}]
    added, deferred = await upstox_feed.apply_discovery_selection(st, selected)

    assert added == []
    assert deferred == [{"token": "9", "symbol": "IDEA"}]
    assert "9" in st.watchlist and "9" in st.rolling_engine.watchlist
    assert st.stream_manager.stream_equity.subs == []


async def test_added_rows_carry_the_csv_field_names_too():
    """Startup rows use Token/Symbol/Exchange (csv.DictReader over
    watchlist.csv); RollingStateEngine._resolve_symbol reads either case.
    A row missing the capitalised form is one lookup away from the
    empty-symbol registry split fixed in Phase 5."""
    st = _state({})
    await upstox_feed.apply_discovery_selection(
        st, [{"token": "2", "symbol": "BHEL-EQ", "exchange": "NSE"}])
    row = st.rolling_engine.watchlist["2"]
    assert row["Symbol"] == "BHEL-EQ" and row["symbol"] == "BHEL-EQ"
    assert row["Token"] == "2" and row["Exchange"] == "NSE"


async def test_nothing_new_means_no_warmup_and_no_subscribe():
    st = _state({"1": _row("SAIL", "1")})
    await upstox_feed.apply_discovery_selection(
        st, [{"token": "1", "symbol": "SAIL", "exchange": "NSE"}])
    assert st.fetcher.warmed == []
    assert st.stream_manager.stream_equity.subs == []
