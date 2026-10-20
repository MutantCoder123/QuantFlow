"""llm.py: Groq's free tier first, the local model when Groq cannot answer."""
import asyncio

import pytest

import llm


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    llm._blocked_until.clear()
    for i in range(2, 10):                                   # only the keys a test sets
        monkeypatch.delenv(f"GROQ_API_KEY_{i}", raising=False)
    calls = {"groq": [], "ollama": []}

    async def fake_ollama(model, prompt, temperature, json_mode):
        calls["ollama"].append((model, json_mode))
        return '{"from": "local"}'
    monkeypatch.setattr(llm, "_ollama", fake_ollama)
    monkeypatch.setattr(llm, "FALLBACK_MODEL", "ollama:qwen2.5:7b")
    return calls


def _groq_returns(monkeypatch, calls, status=200, headers=None, content='{"from": "groq"}'):
    async def fake_post(url, headers_, payload, timeout):
        calls["groq"].append(payload)
        body = {"choices": [{"message": {"content": content}}]} if status == 200 else {"error": {"message": "x"}}
        return status, headers or {}, body
    monkeypatch.setattr(llm, "_post", fake_post)


def run(coro):
    return asyncio.run(coro)


def test_groq_answers_with_a_key(monkeypatch, _clean):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    _groq_returns(monkeypatch, _clean)
    text, used = run(llm.generate("groq:openai/gpt-oss-120b", "p", json_mode=True))
    assert (text, used) == ('{"from": "groq"}', "groq:openai/gpt-oss-120b")
    p = _clean["groq"][0]
    assert p["model"] == "openai/gpt-oss-120b" and p["response_format"] == {"type": "json_object"}
    assert p["reasoning_effort"] == "low" and not _clean["ollama"]


def test_a_blank_key_goes_straight_to_the_local_model(monkeypatch, _clean):
    monkeypatch.setenv("GROQ_API_KEY", "")
    _groq_returns(monkeypatch, _clean)
    text, used = run(llm.generate("groq:openai/gpt-oss-120b", "p"))
    assert used == "ollama:qwen2.5:7b" and not _clean["groq"]


def test_a_rate_limit_falls_back_and_skips_groq_until_retry_after(monkeypatch, _clean):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    _groq_returns(monkeypatch, _clean, status=429, headers={"retry-after": "120"})
    assert run(llm.generate("groq:openai/gpt-oss-20b", "p"))[1] == "ollama:qwen2.5:7b"
    assert run(llm.generate("groq:openai/gpt-oss-20b", "p"))[1] == "ollama:qwen2.5:7b"
    assert len(_clean["groq"]) == 1                          # the second call did not hit Groq
    _groq_returns(monkeypatch, _clean)                       # the other model has its own limits
    assert run(llm.generate("groq:openai/gpt-oss-120b", "p"))[1] == "groq:openai/gpt-oss-120b"


def test_a_short_rate_limit_is_waited_out_on_groq(monkeypatch, _clean):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    slept = []

    async def fake_sleep(s):
        slept.append(s)
    monkeypatch.setattr(llm.asyncio, "sleep", fake_sleep)
    answers = iter([(429, {"retry-after": "5"}), (200, {})])

    async def fake_post(url, headers_, payload, timeout):
        status, h = next(answers)
        _clean["groq"].append(payload)
        return status, h, {"choices": [{"message": {"content": "ok"}}]} if status == 200 else {}
    monkeypatch.setattr(llm, "_post", fake_post)
    assert run(llm.generate("groq:openai/gpt-oss-120b", "p")) == ("ok", "groq:openai/gpt-oss-120b")
    assert len(_clean["groq"]) == 2 and slept and 0 < slept[0] <= 5 and not _clean["ollama"]


def test_a_server_error_or_network_failure_falls_back(monkeypatch, _clean):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    _groq_returns(monkeypatch, _clean, status=503)
    assert run(llm.generate("groq:openai/gpt-oss-120b", "p"))[1] == "ollama:qwen2.5:7b"

    async def boom(*a, **k):
        raise OSError("no network")
    monkeypatch.setattr(llm, "_post", boom)
    assert run(llm.generate("groq:openai/gpt-oss-120b", "p"))[1] == "ollama:qwen2.5:7b"


def test_local_models_never_touch_groq(monkeypatch, _clean):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    _groq_returns(monkeypatch, _clean)
    assert run(llm.generate_text("ollama:qwen2.5:7b", "p")) == '{"from": "local"}'
    assert not _clean["groq"]


def test_judge_uses_groq_and_shadow_news_use_the_light_model():
    import yaml
    from paths import CONFIG_DIR
    assert yaml.safe_load((CONFIG_DIR / "paper.yaml").read_text(encoding="utf-8"))["judge_model"] == llm.JUDGE_MODEL
    assert llm.JUDGE_MODEL == "groq:openai/gpt-oss-120b" and llm.LIGHT_MODEL == "groq:openai/gpt-oss-20b"
    import inspect
    from data_services import news_feed
    from shadow import reanalysis
    assert "model_name=NEWS_MODEL" in inspect.getsource(news_feed)
    assert "model = SHADOW_MODEL" in inspect.getsource(reanalysis.run_forever)


def test_a_second_key_takes_over_when_the_first_is_out_for_the_day(monkeypatch, _clean):
    monkeypatch.setenv("GROQ_API_KEY", "k1")
    monkeypatch.setenv("GROQ_API_KEY_2", "k2")
    used = []

    async def fake_post(url, headers, payload, timeout):
        key = headers["Authorization"].split()[-1]
        used.append(key)
        if key == "k1":
            return 429, {"retry-after": "1200"}, {}
        return 200, {}, {"choices": [{"message": {"content": "ok"}}]}
    monkeypatch.setattr(llm, "_post", fake_post)
    assert run(llm.generate("groq:openai/gpt-oss-120b", "p")) == ("ok", "groq:openai/gpt-oss-120b")
    assert run(llm.generate("groq:openai/gpt-oss-120b", "p"))[0] == "ok"
    assert used == ["k1", "k2", "k2"]                        # the spent key is skipped afterwards
    assert not _clean["ollama"]


def test_the_local_model_answers_only_when_every_key_is_out(monkeypatch, _clean):
    monkeypatch.setenv("GROQ_API_KEY", "k1")
    monkeypatch.setenv("GROQ_API_KEY_3", "k3")                # gaps in the numbering are fine
    _groq_returns(monkeypatch, _clean, status=429, headers={"retry-after": "900"})
    assert llm.groq_keys() == ["k1", "k3"]
    assert run(llm.generate("groq:openai/gpt-oss-120b", "p"))[1] == "ollama:qwen2.5:7b"
    assert len(_clean["groq"]) == 2                          # each key tried once
