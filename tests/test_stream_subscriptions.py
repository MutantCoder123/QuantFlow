"""Symbols added after startup must survive a reconnect.

_supervise re-subscribes on every reconnect -- to the key list it was given
at startup. A symbol added later (manual add, or Discovery) was subscribed
once, directly: that raised if the socket was down, and even when it
worked the symbol silently fell off the live feed at the next reconnect.
Found by the first end-to-end Discovery run (weekend, sockets closed).
"""
from data_services.upstox_feed import UpstoxStreamManager


class _Streamer:
    def __init__(self, open_=True):
        self.open, self.subs = open_, []

    def subscribe(self, keys, mode):
        if not self.open:
            raise Exception("WebSocket is not open.")
        self.subs.append((list(keys), mode))


def _manager(open_=True):
    m = UpstoxStreamManager.__new__(UpstoxStreamManager)
    m.stream_equity, m.stream_options = _Streamer(open_), _Streamer(open_)
    m.subscriptions = {"equity": ["NSE_EQ|A"], "options": []}
    return m


def test_an_added_key_joins_the_list_reconnects_resubscribe():
    m = _manager()
    assert m.add_subscription("equity", ["NSE_EQ|B"]) is True
    assert m.subscriptions["equity"] == ["NSE_EQ|A", "NSE_EQ|B"]
    assert m.stream_equity.subs == [(["NSE_EQ|B"], "full_d30")]


def test_a_closed_socket_defers_to_the_next_reconnect_instead_of_raising():
    m = _manager(open_=False)
    assert m.add_subscription("equity", ["NSE_EQ|B"]) is False
    assert "NSE_EQ|B" in m.subscriptions["equity"]


def test_options_use_their_own_stream_and_mode():
    m = _manager()
    m.add_subscription("options", ["NSE_FO|X"])
    assert m.stream_options.subs == [(["NSE_FO|X"], "option_greeks")]
    assert m.subscriptions["options"] == ["NSE_FO|X"]


def test_a_key_already_subscribed_is_not_duplicated():
    m = _manager()
    m.add_subscription("equity", ["NSE_EQ|A"])
    assert m.subscriptions["equity"] == ["NSE_EQ|A"]
    assert m.stream_equity.subs == []


# -- the supervisor: one socket per stream ------------------------------------
# 2026-09-29, first live session: the SDK's connect() starts its own thread
# and returns at once, so the old supervisor saw a dead thread, called
# connect() again every 1-2 s (each a new socket) while the SDK's own
# auto-reconnect added more -- until Upstox refused the handshake with 403.
import asyncio
import types

import pytest


class _Sock:
    def __init__(self):
        self.connected = True


class _WS:
    def __init__(self, pool):
        self.sock, self.pool = _Sock(), pool
        pool.append(self)

    def close(self):
        self.sock.connected = False


class _LiveStreamer:
    """Mimics MarketDataStreamerV3: connect() returns at once with a new socket."""
    def __init__(self):
        self.sockets, self.handlers, self.auto = [], [], True
        self.feeder = None

    def auto_reconnect(self, enable, *a, **k):
        self.auto = enable

    def on(self, event, fn):
        self.handlers.append(event)

    def connect(self):
        self.feeder = types.SimpleNamespace(ws=_WS(self.sockets))

    def open_count(self):
        return sum(ws.sock.connected for ws in self.sockets)


class _Stop(Exception):
    pass


def _run(streamer, keys, script, max_sleeps=200):
    """Drive _supervise with a fake clock; `script(streamer, n)` runs before each sleep."""
    m = UpstoxStreamManager.__new__(UpstoxStreamManager)
    t = {"now": 0.0, "n": 0, "max_open": 0}

    async def sleep(s):
        t["n"] += 1
        t["now"] += s
        t["max_open"] = max(t["max_open"], streamer.open_count())
        script(streamer, t["n"])
        if t["n"] >= max_sleeps:
            raise _Stop

    with pytest.raises(_Stop):
        asyncio.run(m._supervise("Equity Tape", streamer, keys, "full_d30", sleep=sleep, clock=lambda: t["now"]))
    return t


def test_a_healthy_socket_is_left_alone():
    s = _LiveStreamer()
    t = _run(s, ["NSE_EQ|A"], lambda st, n: None)
    assert len(s.sockets) == 1 and t["max_open"] == 1
    assert s.auto is False                                   # one reconnect owner, not two
    assert s.handlers == ["open", "message", "error", "close"]   # registered once


def test_a_dropped_socket_is_replaced_by_exactly_one_new_one():
    s = _LiveStreamer()

    def drop(st, n):
        if n in (10, 80):
            st.feeder.ws.sock.connected = False              # the exchange closes it
    t = _run(s, ["NSE_EQ|A"], drop)
    assert len(s.sockets) == 3 and t["max_open"] == 1


def test_a_socket_that_never_opens_is_closed_before_the_retry():
    s = _LiveStreamer()
    real = s.connect

    def connect_but_never_open():
        real()
        s.feeder.ws.sock.connected = False
    s.connect = connect_but_never_open
    _run(s, ["NSE_EQ|A"], lambda st, n: None, max_sleeps=120)
    assert s.open_count() == 0 and len(s.sockets) >= 2


def test_a_stream_with_no_keys_opens_no_socket_until_it_gets_some():
    s = _LiveStreamer()
    keys = []

    def add(st, n):
        if n == 5:
            keys.append("NSE_FO|X")                           # add_subscription appends to this list
    _run(s, keys, add, max_sleeps=20)
    assert len(s.sockets) == 1
