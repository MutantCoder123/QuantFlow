"""AI analysis of Discovery's top 10.

The retired playbook asked the LLM for entry/target/stop prices and a
confidence grade -- numbers with nothing behind them. This analysis asks
for reasoning only, and the parser enforces it structurally: there is no
numeric field to fill, a symbol outside the ten cannot enter the result,
and a stock with no cached news cannot be described as news-supported.
"""
import json

import pytest

from discovery_analysis import analyze_top, build_prompt, parse_analysis, TOP_N


def _pick(sym, score, news="Order win", bias="LONG"):
    return {"token": f"T-{sym}", "symbol": sym, "exchange": "NSE", "score": score,
            "directional_bias": bias, "rs": 5.5, "adv_crore": 300.0,
            "latest_bar_high": 101.0, "latest_bar_low": 99.0, "news": news}


PICKS = [_pick(f"S{i}", 100 - i) for i in range(15)]


def _item(sym, align="SUPPORTS"):
    return {"symbol": sym, "thesis": f"{sym} trend intact", "news_alignment": align,
            "risks": ["gap risk"], "watch_for": "volume confirmation"}


# -- prompt -----------------------------------------------------------------
def test_prompt_carries_only_the_top_ten():
    prompt = build_prompt(PICKS[:TOP_N])
    for p in PICKS[:TOP_N]:
        assert f'"{p["symbol"]}"' in prompt
    for p in PICKS[TOP_N:]:
        assert f'"{p["symbol"]}"' not in prompt


def test_prompt_forbids_price_levels_and_confidence_grades():
    prompt = build_prompt(PICKS[:TOP_N]).lower()
    assert "do not" in prompt and "price level" in prompt and "confidence" in prompt


# -- parser -----------------------------------------------------------------
def test_parses_a_well_formed_response():
    text = json.dumps({"items": [_item("S0"), _item("S1", "CONTRADICTS")]})
    out = parse_analysis(text, PICKS[:TOP_N])
    assert out["items"]["S0"]["news_alignment"] == "SUPPORTS"
    assert out["items"]["S1"]["news_alignment"] == "CONTRADICTS"
    assert out["items"]["S0"]["risks"] == ["gap risk"]


def test_a_symbol_outside_the_ten_is_dropped():
    text = json.dumps({"items": [_item("S0"), _item("RELIANCE"), _item("S12")]})
    out = parse_analysis(text, PICKS[:TOP_N])
    assert set(out["items"]) == {"S0"}


def test_symbols_the_model_skipped_are_reported_not_invented():
    text = json.dumps({"items": [_item("S0")]})
    out = parse_analysis(text, PICKS[:TOP_N])
    assert out["missing"] == [p["symbol"] for p in PICKS[1:TOP_N]]


def test_no_cached_news_cannot_be_called_supportive():
    picks = [_pick("S0", 90, news=None)]
    out = parse_analysis(json.dumps({"items": [_item("S0", "SUPPORTS")]}), picks)
    assert out["items"]["S0"]["news_alignment"] == "NO_NEWS"


def test_an_unknown_alignment_value_becomes_none_not_a_guess():
    out = parse_analysis(json.dumps({"items": [_item("S0", "VERY_BULLISH")]}), PICKS[:1])
    assert out["items"]["S0"]["news_alignment"] is None


def test_numeric_fields_the_model_volunteers_are_not_carried_through():
    item = dict(_item("S0"), entry=101.5, target=110.0, stoploss=97.0, confidence="High")
    out = parse_analysis(json.dumps({"items": [item]}), PICKS[:1])
    assert set(out["items"]["S0"]) == {"thesis", "news_alignment", "risks", "watch_for"}


def test_a_code_fenced_response_still_parses():
    text = "```json\n" + json.dumps({"items": [_item("S0")]}) + "\n```"
    assert "S0" in parse_analysis(text, PICKS[:1])["items"]


def test_malformed_output_raises_rather_than_returning_empty():
    with pytest.raises(ValueError):
        parse_analysis("the market looks bullish today", PICKS[:1])


# -- the call ---------------------------------------------------------------
class _FakeModels:
    def __init__(self, text):
        self.text, self.calls = text, []

    async def generate_content(self, model, contents, config=None):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return type("R", (), {"text": self.text})()


class _FakeClient:
    def __init__(self, text):
        self.aio = type("A", (), {})()
        self.aio.models = _FakeModels(text)


async def test_one_call_for_all_ten_and_only_the_ten_go_in():
    client = _FakeClient(json.dumps({"items": [_item(p["symbol"]) for p in PICKS[:TOP_N]]}))
    out = await analyze_top(PICKS, model="gemini-2.5-flash", client=client)
    calls = client.aio.models.calls
    assert len(calls) == 1
    assert calls[0]["model"] == "gemini-2.5-flash"
    assert '"S10"' not in calls[0]["contents"]
    assert len(out["items"]) == TOP_N and out["missing"] == []
    assert out["model"] == "gemini-2.5-flash" and out["generated_at"]


async def test_no_picks_means_no_call():
    client = _FakeClient("{}")
    assert await analyze_top([], model="m", client=client) is None
    assert client.aio.models.calls == []
