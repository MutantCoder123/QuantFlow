"""The score's news part (2026-10-01): it was a hard-coded 0 shown as "no
input". Headlines are now rated once each and combined into a news score
that is measured and shown; it counts in the composite only when the policy
turns it on (conviction.news_in_score)."""
import asyncio

import pytest

from core.news_score import news_score, parse_sentiment
from conviction_scorer import ConvictionScorer

NOW = 1_790_800_000.0


def test_the_models_reply_becomes_a_rating():
    assert parse_sentiment("[BULLISH] - order win") == 1
    assert parse_sentiment("bearish - guidance cut") == -1
    assert parse_sentiment("NEUTRAL - No immediate fundamental catalyst detected.") == 0
    assert parse_sentiment("could not parse") is None and parse_sentiment(None) is None


def test_newer_headlines_count_more_and_no_rated_news_is_unknown():
    fresh_bull = {"sentiment": 1, "published_ts": NOW}
    old_bear = {"sentiment": -1, "published_ts": NOW - 48 * 3600}          # two half-lives: a quarter the weight
    assert news_score([fresh_bull, old_bear], now=NOW) == pytest.approx(0.6)
    assert news_score([{"headline": "x"}], now=NOW) is None             # unrated is not neutral
    assert news_score([], now=NOW) is None and news_score(None) is None
    assert news_score([{"sentiment": 0, "published_ts": NOW}], now=NOW) == 0.0


def _payload(news):
    return {"market_regime": {"current_regime": "RANGE_BOUND_CHOP"},
            "1_live_microstructure": {"flow_divergence_state": {"state": "MOMENTUM_CONFIRMED_BULLISH"}},
            "2_derivatives_matrix_52w": {}, "3_local_structural_edge_20d": {},
            "4_catalyst_engine": {"raw_news": [], "news_score": news}}


def test_news_is_measured_but_not_counted_by_default(monkeypatch):
    monkeypatch.setattr(ConvictionScorer, "_cfg_conviction", staticmethod(lambda k, d: d))
    with_news = ConvictionScorer().score_setup(_payload(0.8), {"ltp": 100.0})
    without = ConvictionScorer().score_setup(_payload(None), {"ltp": 100.0})
    cat = with_news["contributions"]["catalyst"]
    assert cat["raw"] == 0.8 and cat["counted"] is False and cat["dead"] is False
    assert cat["contribution"] == 0.0
    assert with_news["composite_score"] == without["composite_score"]       # the score is unchanged
    assert without["contributions"]["catalyst"]["dead"] is True


def test_a_policy_can_turn_the_news_on(monkeypatch):
    monkeypatch.setattr(ConvictionScorer, "_cfg_conviction",
                        staticmethod(lambda k, d: True if k == "news_in_score" else d))
    on = ConvictionScorer().score_setup(_payload(0.8), {"ltp": 100.0})
    cat = on["contributions"]["catalyst"]
    assert cat["counted"] is True and cat["contribution"] > 0


def test_each_headline_is_rated_once(monkeypatch):
    from news_engine import NewsEngine
    calls = []

    async def fake(cls, headline, summary, model_name="m", symbol=None):
        calls.append((symbol, headline))
        return "BULLISH - fine"
    monkeypatch.setattr(NewsEngine, "analyze_catalyst", classmethod(fake))
    monkeypatch.setattr(NewsEngine, "_ratings", {})
    arts = [{"headline": "Order win", "summary": ""}]
    asyncio.run(NewsEngine._rate("SAIL", arts, "m"))
    asyncio.run(NewsEngine._rate("SAIL", [{"headline": "Order win", "summary": ""}], "m"))
    assert arts[0]["sentiment"] == 1 and calls == [("SAIL", "Order win")]
