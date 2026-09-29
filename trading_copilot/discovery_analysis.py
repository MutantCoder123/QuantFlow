"""AI analysis of Discovery's top picks -- reasoning, not numbers.

The retired playbook asked the LLM for entry/target/stop prices and a
High/Medium/Low confidence, and rendered them as if they were measured.
This asks for commentary on what the screener already measured, and the
parser enforces that structurally rather than trusting the prompt:
  * the only fields carried through are text -- a price or confidence the
    model volunteers has nowhere to land,
  * a symbol outside the analysed set is dropped,
  * a symbol the model skipped is reported as missing, not filled in,
  * output that is not the expected JSON raises -- it is never read as
    "no analysis".

Anything that is really arithmetic is decided by code, not the model.
Measured on qwen2.5:7b: asked to judge relative strength against the
screener's direction it called positive RS "contradicting" a LONG; asked
whether news supported the direction it called good news on a SHORT
"SUPPORTS" in 2 of 3 runs and invented a reason ("may indicate
overvaluation"). So:
  * RS agreement and liquidity are labelled here and handed over as facts;
  * news is judged in a separate pass that never sees the direction -- the
    model only says whether each headline is GOOD, BAD or MIXED for the
    company -- and code turns that into SUPPORTS / CONTRADICTS.
That is at most two calls per scan: the news pass (skipped when none of the
picks has news) and one commentary call over all of them.

Provider is chosen by the model name: "ollama:<tag>" runs on the local
Ollama server (OLLAMA_HOST, default http://127.0.0.1:11434); anything else
is a Gemini model. Both go through the same parsers -- a local model gets
no extra trust. The parsers guard structure, not truth: they cannot catch
a fluent sentence asserting a fact that was never supplied. Small models
do that readily (llama3.2:1b moved one stock's headline onto another), so
model size matters here.
"""
import datetime
import json
import logging
import math
import numbers
import os

import numpy as np
from llm import DEFAULT_MODEL

logger = logging.getLogger(__name__)

TOP_N = 10
SENTIMENTS = ("GOOD", "BAD", "MIXED")
_MAX_TEXT = 400
_MAX_RISKS = 3

# Liquidity bands over 20-session average daily traded value (rupees crore).
# The policy's min_adv_crore floor (50) applies to the watchlist selection,
# not to the top picks -- a pick below it is labelled LOW here.
_LIQUIDITY_BANDS = ((500.0, "HIGH"), (100.0, "MEDIUM"), (0.0, "LOW"))


def _num(v):
    """A plain float, or None. The screener's values are numpy scalars:
    np.float32 is not a float subclass, and a comparison on them yields a
    numpy bool that json.dumps rejects -- which crashed the first live run."""
    if isinstance(v, (bool, np.bool_)) or not isinstance(v, numbers.Real):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def _news_alignment(pick: dict, sentiment: str | None) -> str | None:
    """SUPPORTS / CONTRADICTS / MIXED / NO_NEWS, or None when news exists
    but its sentiment is unknown -- never a guess."""
    if not pick.get("news"):
        return "NO_NEWS"
    if sentiment == "MIXED":
        return "MIXED"
    bias = pick.get("directional_bias")
    if sentiment not in ("GOOD", "BAD") or bias not in ("LONG", "SHORT"):
        return None
    return "SUPPORTS" if (sentiment == "GOOD") == (bias == "LONG") else "CONTRADICTS"


def _facts(pick: dict, sentiment: dict | None = None) -> dict:
    """What the commentary call is given: measured facts, pre-labelled by
    code. Price levels are left out -- the model is not to discuss prices,
    and numbers it is shown are numbers it will talk about."""
    bias = pick.get("directional_bias")
    rs = _num(pick.get("rs"))
    agrees = None
    if rs is not None and bias in ("LONG", "SHORT") and rs != 0:
        agrees = (rs > 0) == (bias == "LONG")
    adv = _num(pick.get("adv_crore"))
    liquidity = None if adv is None else next(lbl for floor, lbl in _LIQUIDITY_BANDS if adv >= floor)
    score = _num(pick.get("score"))
    return {
        "symbol": pick.get("symbol"),
        "screener_direction": bias,
        "screener_score": round(score, 1) if score is not None else None,
        "relative_strength_vs_nifty_pct": round(rs, 2) if rs is not None else None,
        "relative_strength_agrees_with_direction": agrees,
        "liquidity": liquidity,
        "cached_news": pick.get("news") or None,
        "news_vs_direction": _news_alignment(pick, (sentiment or {}).get(pick.get("symbol"))),
    }


# -- pass 1: news, judged without the direction ------------------------------
def build_sentiment_prompt(picks: list) -> str:
    headlines = json.dumps([{"symbol": p.get("symbol"), "news": p.get("news")}
                            for p in picks if p.get("news")], indent=1)
    return (
        "For each company below, decide whether its news is GOOD, BAD or MIXED for the company "
        "itself (its business and its share price). Judge only the news text. MIXED means it "
        "genuinely cuts both ways.\n"
        'Respond with JSON only: {"items": [{"symbol": "...", "news_sentiment": "GOOD|BAD|MIXED"}]}\n\n'
        f"NEWS:\n{headlines}"
    )


def _load_items(text: str) -> list:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else ""
        raw = raw.rsplit("```", 1)[0]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"analysis was not JSON: {e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("analysis JSON has no 'items' list")
    return [it for it in data["items"] if isinstance(it, dict)]


def parse_sentiment(text: str, symbols: list) -> dict:
    """{symbol: GOOD|BAD|MIXED|None} for exactly the symbols asked about."""
    out = {s: None for s in symbols}
    for it in _load_items(text):
        sym = it.get("symbol")
        if sym in out and out[sym] is None:
            label = it.get("news_sentiment")
            out[sym] = label if label in SENTIMENTS else None
    return out


# -- pass 2: commentary --------------------------------------------------------
def build_prompt(picks: list, sentiment: dict | None = None) -> str:
    facts = json.dumps([_facts(p, sentiment) for p in picks], indent=1)
    return (
        "You review the output of a deterministic stock screener. Each candidate below was "
        "scored by the screener, which also assigned its direction. Your job is commentary on "
        "that evidence -- not a new trade plan.\n\n"
        "For EACH candidate return:\n"
        "- thesis: 1-2 sentences on whether the facts below hang together behind the screener's "
        "direction.\n"
        "- risks: up to 3 short phrases.\n"
        "- watch_for: one short phrase naming what would confirm or break the thesis.\n\n"
        "What the fields mean (the true/false and label fields are already computed -- use them "
        "as given, do not re-derive them):\n"
        "- relative_strength_vs_nifty_pct: recent PRICE performance vs the Nifty 50 index. It is "
        "not a fundamental measure.\n"
        "- relative_strength_agrees_with_direction: true means price performance points the same "
        "way as the screener's direction; false means it points the other way.\n"
        "- liquidity: how heavily the stock trades. It says nothing about the business.\n"
        "- cached_news: the only non-price information. null means none was available.\n"
        "- news_vs_direction: SUPPORTS or CONTRADICTS the screener's direction, MIXED, NO_NEWS, "
        "or null if unknown.\n"
        "There is NO fundamental data here (no earnings, revenue, valuation or balance sheet). Do "
        "not make claims about fundamentals unless cached_news states them.\n\n"
        "Rules:\n"
        "- Use only the facts provided. If they are thin, say so plainly.\n"
        "- Be specific to each stock; do not repeat the same risks or watch_for across stocks "
        "unless they genuinely apply.\n"
        "- Do not state any price level, entry, target or stop.\n"
        "- Do not give a confidence grade or a probability.\n"
        "- Do not change the screener's direction.\n"
        "- Keep each symbol exactly as given.\n\n"
        'Respond with JSON only: {"items": [{"symbol": "...", "thesis": "...", '
        '"risks": ["..."], "watch_for": "..."}]}\n\n'
        f"CANDIDATES:\n{facts}"
    )


def _text(v) -> str | None:
    if not isinstance(v, str) or not v.strip():
        return None
    return v.strip()[:_MAX_TEXT]


def parse_analysis(text: str, picks: list, sentiment: dict | None = None) -> dict:
    """Validate the commentary JSON against the picks it was given.
    news_alignment comes from `sentiment` and the direction -- anything the
    model says about alignment is ignored."""
    by_symbol = {p.get("symbol"): p for p in picks}
    sentiment = sentiment or {}
    items = {}
    for it in _load_items(text):
        sym = it.get("symbol")
        if sym not in by_symbol or sym in items:
            continue
        risks = it.get("risks") if isinstance(it.get("risks"), list) else []
        items[sym] = {
            "thesis": _text(it.get("thesis")),
            "news_alignment": _news_alignment(by_symbol[sym], sentiment.get(sym)),
            "risks": [r for r in (_text(x) for x in risks) if r][:_MAX_RISKS],
            "watch_for": _text(it.get("watch_for")),
        }
    missing = [p.get("symbol") for p in picks if p.get("symbol") not in items]
    return {"items": items, "missing": missing}


# -- providers -----------------------------------------------------------------
OLLAMA_PREFIX = "ollama:"
_OLLAMA_TIMEOUT_S = 300


async def _aiohttp_post(url: str, payload: dict, timeout: float) -> dict:
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload,
                                timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Ollama HTTP {resp.status}: {(await resp.text())[:200]}")
            return await resp.json()


async def _ollama_generate(tag: str, prompt: str, http_post) -> str:
    host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    payload = {
        "model": tag,
        "messages": [{"role": "user", "content": prompt}],
        "format": "json",
        "stream": False,
        # The ten-candidate prompt plus its answer overflows Ollama's
        # 4096-token default context, which truncates silently.
        "options": {"temperature": 0.2, "num_ctx": 8192},
    }
    data = await http_post(f"{host}/api/chat", payload, _OLLAMA_TIMEOUT_S)
    return (data.get("message") or {}).get("content", "")


async def _aiohttp_get(url: str, timeout: float) -> dict:
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            return await resp.json()


async def _ollama_placement(tag: str, http_get=None) -> str | None:
    """Where Ollama actually loaded `tag`: "GPU", "CPU", "partial GPU (n%)",
    or None if unknown. On a hybrid-GPU laptop Ollama can silently fall back
    to the CPU after a sleep -- on 2026-09-28 the only symptom was a 169 s
    analysis instead of ~20 s -- so this is reported rather than assumed."""
    host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    try:
        data = await (http_get or _aiohttp_get)(f"{host}/api/ps", 5)
        m = next(m for m in data.get("models", []) if m.get("name") == tag)
        size, vram = float(m.get("size") or 0), float(m.get("size_vram") or 0)
    except Exception:
        return None
    if size <= 0:
        return None
    if vram >= size:
        return "GPU"
    if vram <= 0:
        return "CPU"
    return f"partial GPU ({round(100 * vram / size)}%)"


def _generator(model: str, client, http_post):
    """prompt -> JSON text, for whichever provider `model` names."""
    if model.startswith(OLLAMA_PREFIX):
        tag = model[len(OLLAMA_PREFIX):]

        async def gen(prompt):
            return await _ollama_generate(tag, prompt, http_post or _aiohttp_post)
        return gen

    if client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        from google import genai
        client = genai.Client(api_key=api_key)
    from google.genai import types
    config = types.GenerateContentConfig(response_mime_type="application/json")

    async def gen(prompt):
        response = await client.aio.models.generate_content(model=model, contents=prompt, config=config)
        return response.text
    return gen


_NEWS_FETCH_CONCURRENCY = 3


async def fill_news(picks: list, fetch) -> None:
    """Fetch news for top-TOP_N picks that had none at scan time.

    The news process only watches symbols already on the watchlist, so most
    Discovery picks arrive with no news. `fetch(symbol)` returns a
    news_engine entry ({"raw_news": [...]}). Mutates the picks in place and
    tags `news_source` -- "scan" (it counted toward the score) or "fetched"
    (it arrived after scoring and did not). Nothing found, or a failed
    fetch, leaves news unknown; it never blocks the analysis.
    """
    import asyncio
    from screener_engine import format_raw_news

    top = list(picks or [])[:TOP_N]
    for p in top:
        if p.get("news"):
            p["news_source"] = "scan"
    gate = asyncio.Semaphore(_NEWS_FETCH_CONCURRENCY)

    async def one(p):
        async with gate:
            try:
                text = format_raw_news(await fetch(p.get("symbol")))
            except Exception as e:
                logger.warning(f"Discovery: news fetch for {p.get('symbol')} failed: {e}")
                return
        if text:
            p["news"], p["news_source"] = text, "fetched"

    await asyncio.gather(*(one(p) for p in top if not p.get("news")))


async def analyze_top(picks: list, model: str = DEFAULT_MODEL, client=None,
                      http_post=None, http_get=None):
    """Analyse the top TOP_N picks: a direction-blind news pass (only if any
    pick has news), then one commentary call. None if there is nothing to
    analyse; raises if a call or its output fails."""
    top = list(picks or [])[:TOP_N]
    if not top:
        return None
    generate = _generator(model, client, http_post)

    with_news = [p for p in top if p.get("news")]
    sentiment = {}
    if with_news:
        logger.info(f"Discovery: news pass ({model}) over {len(with_news)} headlines")
        sentiment = parse_sentiment(await generate(build_sentiment_prompt(with_news)),
                                    [p.get("symbol") for p in with_news])

    logger.info(f"Discovery: commentary call ({model}) over the top {len(top)} picks")
    out = parse_analysis(await generate(build_prompt(top, sentiment)), top, sentiment)
    out["model"] = model
    out["generated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    out["device"] = (await _ollama_placement(model[len(OLLAMA_PREFIX):], http_get)
                     if model.startswith(OLLAMA_PREFIX) else None)
    return out
