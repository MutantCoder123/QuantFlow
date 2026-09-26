"""A stock with no cached catalyst has UNKNOWN news, not "no news".

The screener defaulted news to the string "No fresh news." -- including
when the news process was simply not running and nothing was looked up.
That asserts an absence it never checked, and Discovery forwards it to
the LLM as if it were a real (empty) finding. It is None now.
"""
import numpy as np
import pandas as pd

from screener_engine import PreMarketScreener


def _df(n=60):
    close = pd.Series(np.linspace(100, 120, n))
    return pd.DataFrame({"timestamp": pd.date_range("2026-06-01", periods=n),
                         "open": close, "high": close + 1, "low": close - 1,
                         "close": close, "volume": np.full(n, 1e6)})


class _Fetcher:
    async def _fetch_single(self, client, token, meta, interval, n):
        return None, _df()


def _screener():
    s = PreMarketScreener.__new__(PreMarketScreener)
    s.fetcher, s.smart_connect = _Fetcher(), None
    return s


async def test_no_cached_catalyst_means_news_is_unknown():
    out = await _screener()._process_single_stock("1", {"symbol": "SAIL"}, _df(), {})
    assert out is not None
    assert out["news"] is None


async def test_a_cached_catalyst_is_carried_through():
    cache = {"SAIL": {"summary": "Order win", "sentiment": "POSITIVE", "impact": "HIGH"}}
    out = await _screener()._process_single_stock("1", {"symbol": "SAIL"}, _df(), cache)
    assert out["news"] == "Order win"


# -- the real catalyst-cache shape (news_engine) ----------------------------
# Per-stock entries are {"raw_news": [{"headline", "summary", ...}]} -- never
# summary/sentiment/impact. The screener read those absent keys, defaulted
# them, and so gave every cached stock (empty placeholders included) +5 and
# the text "Fresh news available." while the real headline was dropped.

async def test_an_empty_news_placeholder_is_no_news_and_no_score():
    s = _screener()
    base = await s._process_single_stock("1", {"symbol": "SAIL"}, _df(), {})
    out = await s._process_single_stock("1", {"symbol": "SAIL"}, _df(), {"SAIL": {"raw_news": []}})
    assert out["news"] is None
    assert out["score"] == base["score"]


async def test_real_headlines_reach_the_output():
    cache = {"SAIL": {"raw_news": [
        {"Article": "Article 1", "headline": "SAIL wins Rs 2,000 cr rail order", "summary": "Largest order this year."},
        {"Article": "Article 2", "headline": "Steel prices firm up", "summary": ""},
    ]}}
    s = _screener()
    base = await s._process_single_stock("1", {"symbol": "SAIL"}, _df(), {})
    out = await s._process_single_stock("1", {"symbol": "SAIL"}, _df(), cache)
    assert "SAIL wins Rs 2,000 cr rail order" in out["news"]
    assert "Largest order this year." in out["news"]
    assert "Steel prices firm up" in out["news"]
    assert "Fresh news available" not in out["news"]
    # Presence of a real catalyst still earns the attention bump the old
    # LOW-impact default actually gave in practice; with no sentiment data
    # it must not move the direction.
    assert out["score"] == base["score"] + 5
    assert out["directional_bias"] == base["directional_bias"]
