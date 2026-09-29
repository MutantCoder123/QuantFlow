"""The system's default LLM, and plain-text generation for either provider.

A model name is either a Gemini id ("gemini-2.5-flash") or "ollama:<tag>",
which runs on the local Ollama server (OLLAMA_HOST, default
http://127.0.0.1:11434). The default is the local qwen, so nothing depends
on paid API credits unless a Gemini model is picked explicitly.
Override with QF_DEFAULT_MODEL in .env.
"""
from __future__ import annotations

import os

OLLAMA_PREFIX = "ollama:"
DEFAULT_MODEL = os.getenv("QF_DEFAULT_MODEL") or "ollama:qwen2.5:7b"
_OLLAMA_TIMEOUT_S = 300


def is_local(model: str) -> bool:
    return str(model).startswith(OLLAMA_PREFIX)


async def generate_text(model: str, prompt: str, temperature: float = 0.2) -> str:
    """One prompt in, the model's text out. Raises on a provider failure."""
    if is_local(model):
        import aiohttp
        host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
        payload = {
            "model": model[len(OLLAMA_PREFIX):],
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"temperature": temperature, "num_ctx": 8192},
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{host}/api/chat", json=payload,
                                    timeout=aiohttp.ClientTimeout(total=_OLLAMA_TIMEOUT_S)) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Ollama HTTP {resp.status}: {(await resp.text())[:200]}")
                data = await resp.json()
        return ((data.get("message") or {}).get("content") or "").strip()

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    from google import genai
    response = await genai.Client(api_key=api_key).aio.models.generate_content(model=model, contents=prompt)
    return (response.text or "").strip()
