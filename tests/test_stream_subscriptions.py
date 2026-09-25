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
