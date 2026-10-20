"""The second shadow arm: TypeSafe's Jev answers the same question as the LLM.

Jev returns a typed choice with a probability for each option instead of
text, so its answers can be checked for calibration (when it is 70% sure,
is it right about 70% of the time?) as well as for rupees. It sees the same
position, journal and bars as the LLM arm, laid out as named fields.

Off unless TYPESAFE_API_KEY is set (.env); QF_SHADOW_JEV=0 turns it off.
Cloud API: a failed call is recorded and never retried. JEV_DAILY_CAP bounds
the calls a day.
"""
from __future__ import annotations

import os
import time

from shadow.reanalysis import DECISIONS, MOMENT_WORDS, hm

URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
TIMEOUT_S = 20
JEV_DAILY_CAP = 400

INSTRUCTIONS = (
    "You are reviewing an open intraday paper trade on the Indian NSE cash market, described in "
    "`position`, `journal` (the engine's checks since entry, oldest first), `recent_bars` (5-minute "
    "bars) and `moment` (what just happened). Using the price action, the R multiples and how the "
    "score and regime have moved since entry, decide what to do with the position right now."
)
CRITERIA = {
    "EXIT_NOW": "Close the position at the current price: the move is exhausted or the reason for the "
                "trade has gone, and waiting is more likely to give back profit or deepen the loss.",
    "HOLD": "Keep the position with the stop and target as they are: the trade is still working or "
            "has a fair chance to, and the current stop already limits the risk acceptably.",
    "TIGHTEN_TO_BREAKEVEN": "Keep the position but move the stop to the entry price: the trade is in "
                            "profit and may run further, but a reversal to a loss should not be allowed.",
}


def api_key() -> str | None:
    if os.getenv("QF_SHADOW_JEV", "1") == "0":
        return None
    key = os.getenv("TYPESAFE_API_KEY")
    if not key:
        try:
            from dotenv import find_dotenv, load_dotenv
            load_dotenv(find_dotenv())
            key = os.getenv("TYPESAFE_API_KEY")
        except ImportError:
            pass
    return key or None


def _r(x, nd=2):
    try:
        return round(float(x), nd)
    except (TypeError, ValueError):
        return None


def build_state(moment: dict, o: dict, journal: list[dict], bars: list[dict] | None = None) -> dict:
    ctx = o.get("context") or {}
    state = {
        "position": {
            "stock": o["symbol"], "side": "LONG" if o["side"] == "LONG" else "SHORT",
            "opened_ist": hm(o["ts"]), "entry_price": _r(o["entry_price"]),
            "initial_stop": _r(o["stop"]), "initial_target": _r(o["target"]),
            "one_R_in_rupees_per_share": _r(abs(float(o["entry_price"]) - float(o["stop"]))),
            "at_entry": {"composite_score": ctx.get("composite"), "regime": ctx.get("regime"),
                         "ai_rationale": ctx.get("rationale") or "none"},
        },
        "journal": [{"time_ist": hm(r["ts"]), "event": r.get("kind"), "note": r.get("note", ""),
                     "r_now": _r(r.get("r_now", 0)), "best_r": _r(r.get("best_r", 0)),
                     "score": r.get("score"), "regime": r.get("regime"),
                     "stop": r.get("stop"), "target": r.get("target")} for r in journal[-20:]],
        "moment": {"time_ist": hm(moment["ts"]), "event": moment["kind"],
                   "what_happened": MOMENT_WORDS.get(moment["kind"], moment["kind"]),
                   "price": moment.get("last"), "r_now": _r(moment.get("r_now", 0)),
                   "best_r_so_far": _r(moment.get("best_r", 0)), "stop_in_force": moment.get("stop")},
    }
    if bars:
        state["recent_bars"] = [{"time": b.get("timestamp"), "open": b.get("open"), "high": b.get("high"),
                                 "low": b.get("low"), "close": b.get("close")} for b in bars[-12:]]
    return state


def request_body(state: dict) -> dict:
    return {"model": MODEL, "state": state,
            "questions": {"decision": {"type": "choice", "instructions": INSTRUCTIONS, "criteria": CRITERIA}}}


def parse_response(body: dict) -> dict:
    """{decision, confidence (0-1), probabilities, reason} from a /v1/systemone response."""
    a = ((body or {}).get("answers") or {}).get("decision") or {}
    dec = str(a.get("choice") or "").upper()
    if dec not in DECISIONS:
        return {"decision": None, "confidence": None, "probabilities": None, "reason": "",
                "error": f"no decision in response: {str(body)[:200]}"}
    probs = {k: round(float(v), 3) for k, v in (a.get("probabilities") or {}).items()}
    return {"decision": dec, "confidence": _r(a.get("confidence"), 3), "probabilities": probs,
            "p_chosen": probs.get(dec),
            "reason": " / ".join(f"{k} {v:.2f}" for k, v in sorted(probs.items(), key=lambda kv: -kv[1])),
            "jev_model": body.get("model"), "input_tokens": (body.get("usage") or {}).get("input_tokens")}


async def ask_jev(moment: dict, o: dict, journal: list[dict], bars: list | None, key: str) -> dict:
    import aiohttp
    t0 = time.time()
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(URL, json=request_body(build_state(moment, o, journal, bars)),
                              headers={"Authorization": f"Bearer {key}"},
                              timeout=aiohttp.ClientTimeout(total=TIMEOUT_S)) as r:
                if r.status != 200:
                    ans = {"decision": None, "confidence": None, "reason": "",
                           "error": f"HTTP {r.status}: {(await r.text())[:200]}"}
                else:
                    ans = parse_response(await r.json())
    except Exception as e:                                   # recorded, never retried in a loop
        ans = {"decision": None, "confidence": None, "reason": "", "error": f"{type(e).__name__}: {e}"}
    ans["latency_s"] = round(time.time() - t0, 2)
    return ans
