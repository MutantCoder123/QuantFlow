"""The engine's state for one stock, in words (the Market board's "Engine"
column, the phone's "Held and signalling" list, the Signals queue)."""
from __future__ import annotations

import json

ENTRY_WORDS = {"LONG": "Long", "SHORT": "Short"}


def parse_report(text) -> dict | None:
    """ReasoningEngine.latest_reports holds a JSON card, or a plain note (an
    error, "no live data"). Only the card is state."""
    if not text:
        return None
    t = str(text).strip()
    for fence in ("```json", "```"):
        if t.startswith(fence):
            t = t[len(fence):]
    if t.endswith("```"):
        t = t[:-3]
    try:
        d = json.loads(t)
    except (TypeError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def _r(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ""
    return f"{'+' if f > 0 else '−' if f < 0 else ''}{abs(f):.2f} R"


def engine_state(report: dict | None, paper: dict | None = None, manual: dict | None = None) -> dict | None:
    """{text, tone: up|down|amber|mute, rank} or None when the engine has
    nothing to say about the stock. `rank` orders the phone list: holdings
    first, then ideas the AI is on, then the rest."""
    if paper:
        side = "short" if str(paper.get("side")).upper() == "SHORT" else "long"
        r = paper.get("r_now")
        try:
            tone = "up" if float(r) > 0 else "down" if float(r) < 0 else "mute"
        except (TypeError, ValueError):
            tone = "mute"
        return {"text": f"Paper {side} · {_r(r)}".rstrip(" ·"), "tone": tone, "rank": 0}
    if manual:
        side = "short" if str(manual.get("direction", "")).lower() == "short" else "long"
        return {"text": f"You’re {side}", "tone": "mute", "rank": 0}
    if not report:
        return None
    tag = report.get("Status_Tag") or ""
    action = str(report.get("Action") or "").upper()
    if report.get("Risk_Rejection") and report.get("Risk_Rejection") != "NO_GEOMETRY":
        return {"text": "Blocked by the risk layer", "tone": "down", "rank": 2}
    if tag == "PENDING_LLM":
        return {"text": "AI reviewing", "tone": "amber", "rank": 1}
    if tag == "LLM_ANALYZED":
        word = ENTRY_WORDS.get(action)
        return {"text": f"AI says {word.lower()}" if word else "AI says wait", "tone": "up" if action == "LONG"
                else "down" if action == "SHORT" else "mute", "rank": 1}
    if tag == "REQUIRED LLM ANALYZE":
        return {"text": "Wants an AI review", "tone": "amber", "rank": 1}
    if tag == "RANK_GATED":
        return {"text": "Below the top-5 cut", "tone": "mute", "rank": 2}
    if tag == "STABILIZING":
        return {"text": "Settling", "tone": "mute", "rank": 2}
    if report.get("Exit_Rule"):
        return {"text": "Exit rule firing", "tone": "amber", "rank": 1}
    return None
