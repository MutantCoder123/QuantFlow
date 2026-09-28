"""AI analysis of Discovery's top 10.

The retired playbook asked the LLM for entry/target/stop prices and a
confidence grade -- numbers with nothing behind them. This analysis asks
for reasoning only, and the parser enforces it structurally: there is no
numeric field to fill, a symbol outside the ten cannot enter the result,
and a stock with no cached news cannot be described as news-supported.
"""
import json

import pytest

from discovery_analysis import (analyze_top, build_prompt, build_sentiment_prompt,
                                parse_analysis, parse_sentiment, TOP_N)


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
    text = json.dumps({"items": [_item("S0"), _item("S1")]})
    out = parse_analysis(text, PICKS[:TOP_N], sentiment={"S0": "GOOD"})
    assert out["items"]["S0"]["thesis"] == "S0 trend intact"
    assert out["items"]["S0"]["risks"] == ["gap risk"]
    assert out["items"]["S0"]["watch_for"] == "volume confirmation"


def test_alignment_is_computed_from_news_sentiment_not_taken_from_the_model():
    """qwen2.5:7b called good news on a SHORT 'SUPPORTS' in 2 of 3 runs and
    invented a reason ('may indicate overvaluation'). The model now only says
    whether news is good or bad for the company, without seeing the
    direction; code compares that with the direction."""
    picks = [_pick("S0", 90, bias="LONG"), _pick("S1", 80, bias="SHORT"),
             _pick("S2", 70, bias="LONG"), _pick("S3", 60, bias="SHORT")]
    text = json.dumps({"items": [_item("S0", "CONTRADICTS"), _item("S1", "SUPPORTS"),
                                 _item("S2", "SUPPORTS"), _item("S3", "CONTRADICTS")]})
    out = parse_analysis(text, picks, sentiment={"S0": "GOOD", "S1": "GOOD", "S2": "MIXED", "S3": "BAD"})
    assert out["items"]["S0"]["news_alignment"] == "SUPPORTS"      # good news, LONG
    assert out["items"]["S1"]["news_alignment"] == "CONTRADICTS"   # good news, SHORT
    assert out["items"]["S2"]["news_alignment"] == "MIXED"
    assert out["items"]["S3"]["news_alignment"] == "SUPPORTS"      # bad news, SHORT


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


def test_news_with_unknown_sentiment_has_no_alignment_rather_than_a_guess():
    out = parse_analysis(json.dumps({"items": [_item("S0", "SUPPORTS")]}), PICKS[:1], sentiment={})
    assert out["items"]["S0"]["news_alignment"] is None


# -- the direction-blind news pass -------------------------------------------
def test_sentiment_prompt_hides_the_screener_direction():
    prompt = build_sentiment_prompt([_pick("S0", 90, news="Order win", bias="SHORT"),
                                     _pick("S1", 80, news="Plant fire", bias="LONG")])
    assert "Order win" in prompt and "Plant fire" in prompt
    assert "SHORT" not in prompt and "LONG" not in prompt


def test_parse_sentiment_accepts_only_known_labels_and_known_symbols():
    text = json.dumps({"items": [{"symbol": "S0", "news_sentiment": "GOOD"},
                                 {"symbol": "S1", "news_sentiment": "GREAT"},
                                 {"symbol": "ZZ", "news_sentiment": "BAD"}]})
    assert parse_sentiment(text, ["S0", "S1"]) == {"S0": "GOOD", "S1": None}


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


def _both(symbols, sentiment="GOOD"):
    """A response that satisfies both passes' parsers."""
    return json.dumps({"items": [dict(_item(s), news_sentiment=sentiment) for s in symbols]})


async def test_a_news_pass_then_one_commentary_call_and_only_the_ten_go_in():
    client = _FakeClient(_both([p["symbol"] for p in PICKS[:TOP_N]]))
    out = await analyze_top(PICKS, model="gemini-2.5-flash", client=client)
    calls = client.aio.models.calls
    assert len(calls) == 2
    sentiment_call, commentary_call = calls
    assert "SHORT" not in sentiment_call["contents"] and "LONG" not in sentiment_call["contents"]
    assert all('"S10"' not in c["contents"] for c in calls)
    assert all(c["model"] == "gemini-2.5-flash" for c in calls)
    assert len(out["items"]) == TOP_N and out["missing"] == []
    assert out["items"]["S0"]["news_alignment"] == "SUPPORTS"     # GOOD news, LONG pick
    assert out["model"] == "gemini-2.5-flash" and out["generated_at"]


async def test_no_news_among_the_ten_means_a_single_call():
    picks = [_pick(f"S{i}", 100 - i, news=None) for i in range(3)]
    client = _FakeClient(_both(["S0", "S1", "S2"]))
    out = await analyze_top(picks, model="gemini-2.5-flash", client=client)
    assert len(client.aio.models.calls) == 1
    assert out["items"]["S0"]["news_alignment"] == "NO_NEWS"


async def test_no_picks_means_no_call():
    client = _FakeClient("{}")
    assert await analyze_top([], model="m", client=client) is None
    assert client.aio.models.calls == []


# -- local models via Ollama ------------------------------------------------
async def test_an_ollama_model_goes_to_the_local_server_not_gemini():
    sent = []

    async def post(url, payload, timeout):
        sent.append((url, payload))
        return {"message": {"content": json.dumps({"items": [_item("S0")]})}}

    gemini = _FakeClient("{}")
    out = await analyze_top(PICKS[:1], model="ollama:qwen2.5:7b", client=gemini, http_post=post)
    assert gemini.aio.models.calls == []
    url, payload = sent[0]
    assert url.endswith("/api/chat")
    assert payload["model"] == "qwen2.5:7b"          # tag keeps its own colon
    assert payload["format"] == "json" and payload["stream"] is False
    assert '"S0"' in payload["messages"][0]["content"]
    assert out["model"] == "ollama:qwen2.5:7b" and "S0" in out["items"]


async def test_local_output_goes_through_the_same_parser():
    """A local model gets no special trust: unknown symbols are dropped and
    volunteered prices discarded exactly as for Gemini."""
    async def post(url, payload, timeout):
        bad = dict(_item("S0"), entry=101.5, confidence="High")
        return {"message": {"content": json.dumps({"items": [bad, _item("RELIANCE")]})}}

    out = await analyze_top(PICKS[:1], model="ollama:llama3.2:1b", http_post=post)
    assert set(out["items"]) == {"S0"}
    assert "entry" not in out["items"]["S0"] and "confidence" not in out["items"]["S0"]


async def test_ollama_host_can_be_overridden(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "http://gpu-box:11434")
    sent = []

    async def post(url, payload, timeout):
        sent.append(url)
        return {"message": {"content": json.dumps({"items": []})}}

    await analyze_top(PICKS[:1], model="ollama:m", http_post=post)
    assert sent and set(sent) == {"http://gpu-box:11434/api/chat"}


# -- facts are labelled by code, not interpreted by the model ---------------
from discovery_analysis import _facts


def test_rs_agreement_is_computed_not_left_to_the_model():
    """qwen2.5:7b called positive RS 'contradicting' a LONG. Whether RS
    agrees with the direction is a sign check -- code does it."""
    assert _facts(dict(_pick("A", 1), directional_bias="LONG", rs=11.4))["relative_strength_agrees_with_direction"] is True
    assert _facts(dict(_pick("A", 1), directional_bias="SHORT", rs=11.0))["relative_strength_agrees_with_direction"] is False
    assert _facts(dict(_pick("A", 1), directional_bias="SHORT", rs=-8.5))["relative_strength_agrees_with_direction"] is True
    assert _facts(dict(_pick("A", 1), rs=None))["relative_strength_agrees_with_direction"] is None


def test_liquidity_is_a_label_with_explicit_thresholds():
    assert _facts(dict(_pick("A", 1), adv_crore=628.9))["liquidity"] == "HIGH"
    assert _facts(dict(_pick("A", 1), adv_crore=142.7))["liquidity"] == "MEDIUM"
    assert _facts(dict(_pick("A", 1), adv_crore=43.7))["liquidity"] == "LOW"
    assert _facts(dict(_pick("A", 1), adv_crore=None))["liquidity"] is None


def test_price_levels_are_not_sent_to_the_model():
    f = _facts(_pick("A", 1))
    assert not any("high" in k or "low" in k for k in f if k != "liquidity")


def test_prompt_says_there_is_no_fundamental_data():
    p = build_prompt(PICKS[:TOP_N]).lower()
    assert "no fundamental data" in p


# -- news for the top 10 before analysis --------------------------------------
# The news process only watches symbols already on the watchlist, so most
# Discovery picks had no news at scan time and the news pass had nothing to
# judge. The top 10 are fetched on demand before the AI runs.
from discovery_analysis import fill_news


def _raw(*headlines):
    return {"raw_news": [{"Article": f"Article {i}", "headline": h, "summary": ""}
                         for i, h in enumerate(headlines, 1)]}


async def test_missing_news_is_fetched_for_the_top_ten_only():
    picks = [_pick(f"S{i}", 100 - i, news=None) for i in range(12)]
    asked = []

    async def fetch(symbol):
        asked.append(symbol)
        return _raw(f"{symbol} wins order")

    await fill_news(picks, fetch)
    assert sorted(asked) == sorted(f"S{i}" for i in range(TOP_N))
    assert picks[0]["news"] == "S0 wins order" and picks[0]["news_source"] == "fetched"
    assert picks[10]["news"] is None


async def test_news_already_cached_at_scan_time_is_not_refetched():
    picks = [_pick("S0", 90, news="Order win"), _pick("S1", 80, news=None)]
    asked = []

    async def fetch(symbol):
        asked.append(symbol)
        return _raw("x")

    await fill_news(picks, fetch)
    assert asked == ["S1"]
    assert picks[0]["news_source"] == "scan"


async def test_nothing_found_or_a_failed_fetch_leaves_news_unknown():
    picks = [_pick("S0", 90, news=None), _pick("S1", 80, news=None)]

    async def fetch(symbol):
        if symbol == "S0":
            return {"raw_news": []}
        raise OSError("news process down")

    await fill_news(picks, fetch)                      # must not raise
    assert picks[0]["news"] is None and picks[1]["news"] is None
    assert "news_source" not in picks[0]


def test_screener_numpy_values_build_a_valid_prompt():
    """The first live run with local AI crashed: the screener's values are
    numpy scalars, `rs > 0` is a numpy bool, and json.dumps rejects it.
    Every fixture above used plain floats, so none of them could see it."""
    import numpy as np
    pick = dict(_pick("S0", 1), score=np.float64(83.65), rs=np.float64(11.01),
                adv_crore=np.float32(142.7), directional_bias="SHORT")
    prompt = build_prompt([pick])                      # must not raise
    facts = json.loads(prompt.split("CANDIDATES:\n", 1)[1])[0]
    assert facts["relative_strength_agrees_with_direction"] is False
    assert facts["liquidity"] == "MEDIUM"              # np.float32 is not a float subclass
    assert facts["screener_score"] == 83.7


# -- where a local model actually ran ------------------------------------------
# On a hybrid-GPU laptop Ollama can silently fall back to the CPU after a
# sleep (2026-09-28: CUDA discovery crashed, qwen2.5:7b ran "100% CPU" and
# took 169 s). The only symptom was slowness, so the placement is reported.
from discovery_analysis import _ollama_placement


@pytest.mark.parametrize("size, vram, want", [
    (5_000_000_000, 5_000_000_000, "GPU"),
    (5_000_000_000, 0, "CPU"),
    (5_000_000_000, 2_500_000_000, "partial GPU (50%)"),
])
async def test_placement_is_read_from_ollama(size, vram, want):
    async def get(url, timeout):
        assert url.endswith("/api/ps")
        return {"models": [{"name": "other:1b", "size": 1, "size_vram": 1},
                           {"name": "qwen2.5:7b", "size": size, "size_vram": vram}]}
    assert await _ollama_placement("qwen2.5:7b", get) == want


async def test_placement_unknown_is_none_not_a_guess():
    async def get(url, timeout):
        raise OSError("down")
    assert await _ollama_placement("qwen2.5:7b", get) is None


async def test_local_analysis_reports_its_placement():
    async def post(url, payload, timeout):
        return {"message": {"content": json.dumps({"items": [dict(_item("S0"), news_sentiment="GOOD")]})}}

    async def get(url, timeout):
        return {"models": [{"name": "qwen2.5:7b", "size": 10, "size_vram": 0}]}

    out = await analyze_top(PICKS[:1], model="ollama:qwen2.5:7b", http_post=post, http_get=get)
    assert out["device"] == "CPU"
