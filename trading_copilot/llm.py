"""The system's LLMs, and plain-text generation for any provider.

A model name is one of
    "groq:<id>"    Groq's hosted models (GROQ_API_KEY), e.g. groq:openai/gpt-oss-120b
    "ollama:<tag>" the local Ollama server (OLLAMA_HOST, default http://127.0.0.1:11434)
    a Gemini id    e.g. gemini-2.5-flash (GEMINI_API_KEY)

Groq's free tier (2026-10) allows, per model, 30 requests/min, 1,000/day, 8K tokens/min and
200K tokens/day. A judge call is ~3-4K tokens, so a busy session outruns it. A Groq call that
has no key, hits a limit (429) or fails goes to FALLBACK_MODEL (the local qwen) instead. After
a 429 the model is skipped until Groq's retry-after has passed. The work is spread over two
models, each with its own limits:
    JUDGE_MODEL  the AI judge and Discovery     (config/paper.yaml judge_model)
    LIGHT_MODEL  shadow re-analysis, news rating (QF_SHADOW_MODEL, QF_NEWS_MODEL)
With GROQ_API_KEY blank everything runs on the local model, as before.
More keys (GROQ_API_KEY_2 .. GROQ_API_KEY_9, user's choice 2026-10-13) are used in order: a key
whose daily budget is spent for a model is skipped until Groq's retry-after; the local model
answers only when every key is out.
Overrides in .env: QF_DEFAULT_MODEL, QF_FALLBACK_MODEL, QF_SHADOW_MODEL, QF_NEWS_MODEL.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time

try:                                     # the key lives in the repo's .env; read it before the defaults
    from dotenv import load_dotenv
    from pathlib import Path
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except Exception:
    pass

logger = logging.getLogger(__name__)

OLLAMA_PREFIX = "ollama:"
GROQ_PREFIX = "groq:"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
LOCAL_MODEL = "ollama:qwen2.5:7b"
JUDGE_MODEL = "groq:openai/gpt-oss-120b"
LIGHT_MODEL = "groq:openai/gpt-oss-20b"
FALLBACK_MODEL = os.getenv("QF_FALLBACK_MODEL") or LOCAL_MODEL
DEFAULT_MODEL = os.getenv("QF_DEFAULT_MODEL") or JUDGE_MODEL
SHADOW_MODEL = os.getenv("QF_SHADOW_MODEL") or LIGHT_MODEL
NEWS_MODEL = os.getenv("QF_NEWS_MODEL") or LIGHT_MODEL
_OLLAMA_TIMEOUT_S = 300
_GROQ_TIMEOUT_S = 60
_GROQ_MAX_TOKENS = 4096                  # gpt-oss's reasoning counts against this
_SHORT_WAIT_S = 20.0                     # a 429 asking for this long or less is waited out ...
_GROQ_TRIES = 3                          # ... up to this many attempts, then the fallback

_blocked_until: dict[tuple[str, int], float] = {}   # (groq model, key index) -> usable again at


class GroqUnavailable(RuntimeError):
    """No key, a rate limit, or a failed call: the caller falls back."""


def is_local(model: str) -> bool:
    return str(model).startswith(OLLAMA_PREFIX)


def is_groq(model: str) -> bool:
    return str(model).startswith(GROQ_PREFIX)


def groq_keys() -> list[str]:
    """GROQ_API_KEY, then GROQ_API_KEY_2 .. GROQ_API_KEY_9, blanks skipped, in that order."""
    names = ["GROQ_API_KEY"] + [f"GROQ_API_KEY_{i}" for i in range(2, 10)]
    keys = [(os.getenv(n) or "").strip() for n in names]
    return [k for k in keys if k]


def groq_key() -> str | None:
    keys = groq_keys()
    return keys[0] if keys else None


async def _post(url: str, headers: dict, payload: dict, timeout: float) -> tuple[int, dict, dict | str]:
    """(status, response headers, JSON body or text). Replaced in tests."""
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers,
                                timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            try:
                body = await resp.json(content_type=None)
            except Exception:
                body = await resp.text()
            return resp.status, dict(resp.headers), body


def _retry_after(headers: dict) -> float:
    for k in ("retry-after", "Retry-After"):
        try:
            return max(1.0, float(headers.get(k)))
        except (TypeError, ValueError):
            continue
    return 60.0


async def _groq(model: str, prompt: str, temperature: float, json_mode: bool) -> str:
    keys = groq_keys()
    if not keys:
        raise GroqUnavailable("GROQ_API_KEY is not set")
    mid = model[len(GROQ_PREFIX):]
    payload = {"model": mid, "messages": [{"role": "user", "content": prompt}],
               "temperature": temperature, "max_completion_tokens": _GROQ_MAX_TOKENS}
    if mid.startswith("openai/gpt-oss"):
        payload["reasoning_effort"] = "low"          # fewer hidden tokens against the daily budget
        payload["include_reasoning"] = False
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    for attempt in range(_GROQ_TRIES * len(keys)):
        # The key that is free soonest (keys in order when several are free). A short block is
        # waited out: a burst of judge calls overruns 8K tokens/min and Groq asks for a few
        # seconds (2026-10-13 09:45: retry in 5 s -> PRESTIGE went to the local model). When
        # every key is blocked for longer (daily budgets spent), the fallback answers at once.
        now = time.time()
        i = min(range(len(keys)), key=lambda j: (max(0.0, _blocked_until.get((model, j), 0.0) - now), j))
        left = _blocked_until.get((model, i), 0.0) - now
        if left > _SHORT_WAIT_S:
            raise GroqUnavailable(f"{model} rate-limited on all {len(keys)} key(s) for {left:.0f}s more")
        if left > 0:
            await asyncio.sleep(left)
        try:
            status, headers, body = await _post(GROQ_URL, {"Authorization": f"Bearer {keys[i]}"}, payload,
                                                _GROQ_TIMEOUT_S)
        except Exception as e:
            raise GroqUnavailable(f"{type(e).__name__}: {e}") from e
        if status != 429:
            break
        wait = _retry_after(headers)
        _blocked_until[(model, i)] = max(_blocked_until.get((model, i), 0.0), time.time() + wait)
        if len(keys) > 1 and wait > _SHORT_WAIT_S:
            logger.info(f"{model}: key {i + 1} of {len(keys)} rate-limited for {wait:.0f}s; trying the next")
    else:
        raise GroqUnavailable(f"{model} rate limit (429) on all {len(keys)} key(s)")
    if status != 200 or not isinstance(body, dict):
        msg = body.get("error", {}).get("message") if isinstance(body, dict) else str(body)
        raise GroqUnavailable(f"Groq HTTP {status}: {str(msg)[:200]}")
    return (((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()


async def _ollama(model: str, prompt: str, temperature: float, json_mode: bool) -> str:
    import aiohttp
    host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    payload = {
        "model": model[len(OLLAMA_PREFIX):],
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": temperature, "num_ctx": 8192},
    }
    if json_mode:
        payload["format"] = "json"
    async with aiohttp.ClientSession() as session:
        async with session.post(f"{host}/api/chat", json=payload,
                                timeout=aiohttp.ClientTimeout(total=_OLLAMA_TIMEOUT_S)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Ollama HTTP {resp.status}: {(await resp.text())[:200]}")
            data = await resp.json()
    return ((data.get("message") or {}).get("content") or "").strip()


async def _gemini(model: str, prompt: str, json_mode: bool) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    from google import genai
    kw = {}
    if json_mode:
        from google.genai import types
        kw["config"] = types.GenerateContentConfig(response_mime_type="application/json")
    response = await genai.Client(api_key=api_key).aio.models.generate_content(model=model, contents=prompt, **kw)
    return (response.text or "").strip()


async def generate(model: str, prompt: str, temperature: float = 0.2,
                   json_mode: bool = False) -> tuple[str, str]:
    """(text, the model that answered). A Groq model that cannot answer hands
    the prompt to FALLBACK_MODEL. Raises when the model that ran fails."""
    if is_groq(model):
        try:
            return await _groq(model, prompt, temperature, json_mode), model
        except GroqUnavailable as e:
            if not groq_key():
                logger.debug(f"{model}: no GROQ_API_KEY, using {FALLBACK_MODEL}")
            else:
                logger.warning(f"{model} unavailable ({e}); using {FALLBACK_MODEL}")
            if is_groq(FALLBACK_MODEL):
                raise
            model = FALLBACK_MODEL
    if is_local(model):
        return await _ollama(model, prompt, temperature, json_mode), model
    return await _gemini(model, prompt, json_mode), model


async def generate_text(model: str, prompt: str, temperature: float = 0.2, json_mode: bool = False) -> str:
    """One prompt in, the model's text out. Raises on a provider failure."""
    return (await generate(model, prompt, temperature, json_mode))[0]
