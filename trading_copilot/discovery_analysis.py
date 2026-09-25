"""AI analysis of Discovery's top picks -- reasoning, not numbers.

The retired playbook asked the LLM for entry/target/stop prices and a
High/Medium/Low confidence, and rendered them as if they were measured.
This asks for commentary on what the screener already measured, and the
parser enforces that structurally rather than trusting the prompt:
  * the only fields carried through are text -- a price or confidence the
    model volunteers has nowhere to land,
  * a symbol outside the analysed set is dropped,
  * a stock with no cached news cannot come back "news-supported",
  * a symbol the model skipped is reported as missing, not filled in,
  * output that is not the expected JSON raises -- it is never read as
    "no analysis".
One call covers all of them: the screener has already done the filtering,
so the model is spent only on the handful worth a second look.
"""
import datetime
import json
import logging
import os

logger = logging.getLogger(__name__)

TOP_N = 10
ALIGNMENTS = ("SUPPORTS", "CONTRADICTS", "NO_NEWS")
_MAX_TEXT = 400
_MAX_RISKS = 3


def _facts(pick: dict) -> dict:
    """The measured fields the model is allowed to reason about."""
    return {
        "symbol": pick.get("symbol"),
        "screener_direction": pick.get("directional_bias"),
        "screener_score": pick.get("score"),
        "relative_strength_vs_nifty_pct": pick.get("rs"),
        "avg_daily_traded_value_crore": pick.get("adv_crore"),
        "latest_daily_bar_high": pick.get("latest_bar_high"),
        "latest_daily_bar_low": pick.get("latest_bar_low"),
        "cached_news": pick.get("news") or None,
    }


def build_prompt(picks: list) -> str:
    facts = json.dumps([_facts(p) for p in picks], indent=1)
    return (
        "You review the output of a deterministic stock screener. Each candidate below was "
        "scored by the screener, which also assigned its direction. Your job is commentary on "
        "that evidence -- not a new trade plan.\n\n"
        "For EACH candidate return:\n"
        "- thesis: 1-2 sentences on why the measured facts do or do not hang together.\n"
        "- news_alignment: SUPPORTS if the cached news backs the screener's direction, "
        "CONTRADICTS if it cuts against it, NO_NEWS if cached_news is null.\n"
        "- risks: up to 3 short phrases.\n"
        "- watch_for: one short phrase naming what would confirm or break the thesis.\n\n"
        "Rules:\n"
        "- Use only the facts provided. If they are thin, say so.\n"
        "- Do not state any price level, entry, target or stop.\n"
        "- Do not give a confidence grade or a probability.\n"
        "- Do not change the screener's direction.\n"
        "- Keep each symbol exactly as given.\n\n"
        'Respond with JSON only: {"items": [{"symbol": "...", "thesis": "...", '
        '"news_alignment": "SUPPORTS|CONTRADICTS|NO_NEWS", "risks": ["..."], "watch_for": "..."}]}\n\n'
        f"CANDIDATES:\n{facts}"
    )


def _text(v) -> str | None:
    if not isinstance(v, str) or not v.strip():
        return None
    return v.strip()[:_MAX_TEXT]


def parse_analysis(text: str, picks: list) -> dict:
    """Validate the model's JSON against the picks it was given."""
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

    by_symbol = {p.get("symbol"): p for p in picks}
    items = {}
    for it in data["items"]:
        if not isinstance(it, dict):
            continue
        sym = it.get("symbol")
        if sym not in by_symbol or sym in items:
            continue
        align = it.get("news_alignment")
        align = align if align in ALIGNMENTS else None
        if not by_symbol[sym].get("news"):
            align = "NO_NEWS"          # nothing cached, so nothing to agree with
        risks = it.get("risks") if isinstance(it.get("risks"), list) else []
        items[sym] = {
            "thesis": _text(it.get("thesis")),
            "news_alignment": align,
            "risks": [r for r in (_text(x) for x in risks) if r][:_MAX_RISKS],
            "watch_for": _text(it.get("watch_for")),
        }
    missing = [p.get("symbol") for p in picks if p.get("symbol") not in items]
    return {"items": items, "missing": missing}


async def analyze_top(picks: list, model: str = "gemini-2.5-flash", client=None):
    """One LLM call over the top TOP_N picks. None if there is nothing to
    analyse; raises if the call or its output fails."""
    top = list(picks or [])[:TOP_N]
    if not top:
        return None
    if client is None:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")
        from google import genai
        client = genai.Client(api_key=api_key)

    from google.genai import types
    config = types.GenerateContentConfig(response_mime_type="application/json")
    logger.info(f"Discovery: one {model} call over the top {len(top)} picks")
    response = await client.aio.models.generate_content(
        model=model, contents=build_prompt(top), config=config)
    out = parse_analysis(response.text, top)
    out["model"] = model
    out["generated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    return out
