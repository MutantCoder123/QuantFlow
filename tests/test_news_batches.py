"""Upstox news takes at most 30 instrument keys a request (UDAPI1193); a
42-stock watchlist was refused whole, every 2 minutes."""
import asyncio

from news_engine import NEWS_KEYS_PER_CALL, NewsEngine


class FakeNewsApi:
    def __init__(self, refuse=()):
        self.calls, self.refuse = [], set(refuse)

    def get_news(self, category, instrument_keys):
        keys = instrument_keys.split(",")
        self.calls.append(keys)
        if len(keys) > 30 or keys[0] in self.refuse:
            e = Exception("(400)")
            e.status, e.body = 400, b'{"errorCode":"UDAPI1193"}'
            raise e
        return type("R", (), {"data": {k: [{"heading": k}] for k in keys}})()


def test_a_long_watchlist_is_asked_30_keys_at_a_time():
    api = FakeNewsApi()
    keys = [f"NSE_EQ|K{i}" for i in range(42)]
    got = asyncio.run(NewsEngine._news_batches(api, keys))
    assert NEWS_KEYS_PER_CALL == 30 and [len(c) for c in api.calls] == [30, 12]
    assert set(got) == set(keys)


def test_a_refused_batch_does_not_lose_the_others():
    api = FakeNewsApi(refuse={"NSE_EQ|K0"})
    got = asyncio.run(NewsEngine._news_batches(api, [f"NSE_EQ|K{i}" for i in range(42)]))
    assert set(got) == {f"NSE_EQ|K{i}" for i in range(30, 42)}
