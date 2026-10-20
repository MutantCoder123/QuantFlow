"""AI re-analysis, in shadow: at each key moment of a paper position, ask the
AI what it would do -- and only record the answer.

The paper engine (policy v4/v5) journals every decision as a REVIEW event.
This process reads those, and at the moments that matter it gives the AI the
position's whole journal (what the engine thought at entry, every check since)
and asks: exit now, hold, or tighten the stop to breakeven? The answer goes to
data/shadow/reanalysis_<day>.jsonl. It never touches a position.

A second arm asks TypeSafe's Jev the same question at the same moment
(shadow/jev.py; on when TYPESAFE_API_KEY is set). Records carry `arm`.

After the close, shadow/report.py scores every answer against what really
happened (exchange 1-minute candles): would following the AI have made or
lost money compared with the engine? That decides, on evidence, whether the
AI earns a place in the exit path (FUTURE_UPDATES.md 4.3).

Run: python trading_copilot/shadow/reanalysis.py   (run.py starts it as the
"shadow" service; set QF_SHADOW=0 to keep it idle).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
APP = HERE.parent
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

logger = logging.getLogger("shadow.reanalysis")
IST = ZoneInfo("Asia/Kolkata")

# The journal entries worth a second opinion. CHECK is sampled (see CHECK_EVERY_S)
# so a quiet position still gets an occasional "would you still hold this?".
MOMENTS = {"RUN_ON", "TARGET_PULLED", "THESIS_AGAINST", "FADE_TIGHTEN", "CHECK"}
CHECK_EVERY_S = 30 * 60
POLL_S = 15
DECISIONS = ("EXIT_NOW", "HOLD", "TIGHTEN_TO_BREAKEVEN")
# what the engine itself did at each moment, to measure agreement
ENGINE_DID = {"RUN_ON": "HOLD", "TARGET_PULLED": "HOLD", "THESIS_AGAINST": "HOLD",
              "FADE_TIGHTEN": "TIGHTEN_TO_BREAKEVEN", "CHECK": "HOLD"}
MOMENT_WORDS = {
    "RUN_ON": "The price has just reached the target. The engine is letting it run, with the stop 1R behind the best price.",
    "TARGET_PULLED": "The original target was judged out of reach (more than 3 x ATR15 away) and has been brought closer.",
    "THESIS_AGAINST": "The live score has just turned against the position.",
    "FADE_TIGHTEN": "The score's support for the trade has stayed below half its entry value for 10 minutes; the engine moved the stop to breakeven.",
    "CHECK": "Routine check of an open position.",
}


def shadow_dir() -> Path:
    from paths import DATA_DIR
    return DATA_DIR / "shadow"


def ist_day(ts: float) -> str:
    return datetime.fromtimestamp(ts, IST).strftime("%Y-%m-%d")


def hm(ts: float) -> str:
    return datetime.fromtimestamp(ts, IST).strftime("%H:%M")


# ----------------------------------------------------------------- pure parts
def pending_moments(events: list[dict], done: set, last_check: dict) -> list[tuple[dict, dict, list]]:
    """(moment REVIEW, its OPEN, the journal up to it) for every journal entry
    not yet asked about, for positions still open. CHECKs are sampled: at most
    one per position per CHECK_EVERY_S. `done` holds review ids already asked;
    `last_check` maps pos_id -> ts of the last CHECK asked (updated here)."""
    opens = {e["pos_id"]: e for e in events if e.get("type") == "OPEN"}
    closed = {e["pos_id"] for e in events if e.get("type") == "CLOSE"}
    by_pos: dict = {}
    out = []
    for e in sorted(events, key=lambda e: e.get("ts", 0)):
        if e.get("type") != "REVIEW":
            continue
        by_pos.setdefault(e["pos_id"], []).append(e)
        if e.get("id") in done or e.get("kind") not in MOMENTS or e["pos_id"] in closed:
            continue
        if e["pos_id"] not in opens:
            continue
        if e["kind"] == "CHECK":
            if e["ts"] - last_check.get(e["pos_id"], float("-inf")) < CHECK_EVERY_S:
                continue
            last_check[e["pos_id"]] = e["ts"]
        out.append((e, opens[e["pos_id"]], list(by_pos[e["pos_id"]])))
    return out


def merge_pending(by_arm: dict[str, list]) -> list[tuple[dict, dict, list, list]]:
    """(moment, OPEN, journal, arms still to ask) per moment, oldest first; arms
    keep the order of `by_arm` (the fast one first)."""
    merged: dict = {}
    for arm, items in by_arm.items():
        for m, o, j in items:
            merged.setdefault(m.get("id"), (m, o, j, []))[3].append(arm)
    return sorted(merged.values(), key=lambda x: x[0].get("ts", 0))


def build_prompt(moment: dict, o: dict, journal: list[dict], bars: list[dict] | None = None) -> str:
    ctx = o.get("context") or {}
    side = "LONG" if o["side"] == "LONG" else "SHORT"
    lines = [
        "You are reviewing an open intraday paper trade on the Indian NSE cash market.",
        "Decide what you would do RIGHT NOW. You do not control the trade; your answer is recorded and scored later.",
        "",
        f"Stock: {o['symbol']}. Side: {side}. Opened {hm(o['ts'])} IST at {o['entry_price']:.2f}.",
        f"Initial stop {o['stop']:.2f}, initial target {o['target']:.2f} (1R = {abs(o['entry_price'] - o['stop']):.2f}).",
        f"At entry: composite score {ctx.get('composite')}, regime {ctx.get('regime')}, "
        f"AI rationale: {ctx.get('rationale') or 'none'}.",
        "",
        "Journal since entry (oldest first):",
    ]
    for r in journal[-20:]:
        lines.append(f"- {hm(r['ts'])} {r.get('kind')}: {r.get('note', '')}; now {r.get('r_now', 0):+.2f}R, "
                     f"best {r.get('best_r', 0):+.2f}R, score {r.get('score')}, regime {r.get('regime')}, "
                     f"stop {r.get('stop')}, target {r.get('target')}")
    if bars:
        lines += ["", "Last 5-minute bars (time open high low close):"]
        lines += [f"- {b.get('timestamp')} {b.get('open')} {b.get('high')} {b.get('low')} {b.get('close')}"
                  for b in bars[-12:]]
    lines += [
        "",
        f"This moment ({hm(moment['ts'])} IST): {MOMENT_WORDS.get(moment['kind'], moment['kind'])}",
        f"Price {moment.get('last')}, {moment.get('r_now', 0):+.2f}R now, best so far {moment.get('best_r', 0):+.2f}R. "
        f"Stop in force {moment.get('stop')}.",
        "",
        "Answer with ONE JSON object and nothing else:",
        '{"decision": "EXIT_NOW" | "HOLD" | "TIGHTEN_TO_BREAKEVEN", "confidence": 1-10, "reason": "<one sentence>"}',
    ]
    return "\n".join(lines)


_DECISION = re.compile(r"\b(EXIT_NOW|TIGHTEN_TO_BREAKEVEN|HOLD)\b")


def parse_decision(text: str | None) -> dict | None:
    """The model's JSON answer, or the first decision word it used; None if neither."""
    t = str(text or "")
    m = re.search(r"\{.*\}", t, re.S)
    if m:
        try:
            d = json.loads(m.group(0))
            dec = str(d.get("decision", "")).upper().strip()
            if dec in DECISIONS:
                conf = d.get("confidence")
                return {"decision": dec, "confidence": int(conf) if str(conf).isdigit() else None,
                        "reason": str(d.get("reason") or "")[:400]}
        except (ValueError, TypeError):
            pass
    w = _DECISION.search(t.upper())
    return {"decision": w.group(1), "confidence": None, "reason": t.strip()[:400]} if w else None


# ----------------------------------------------------------------- the process
def _load_done(path: Path) -> dict[str, set]:
    """arm -> review ids already asked. Records from before the Jev arm have no
    `arm`: they are the LLM's."""
    out: dict[str, set] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
            out.setdefault(r.get("arm") or "llm", set()).add(r["review_id"])
        except (ValueError, KeyError):
            continue
    return out


async def _bars(token: str | None) -> list | None:
    if not token:
        return None
    import aiohttp
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("http://127.0.0.1:8001/api/bars", params={"token": token, "n": 12},
                             timeout=aiohttp.ClientTimeout(total=8)) as r:
                return (await r.json()).get("bars")
    except Exception:
        return None


def record(moment, o, journal, ans: dict, model: str, arm: str) -> dict:
    return {
        "ts": time.time(), "review_id": moment.get("id"), "pos_id": o["pos_id"], "symbol": o["symbol"],
        "side": o["side"], "moment": moment["kind"], "moment_ts": moment["ts"], "price": moment.get("last"),
        "r_now": moment.get("r_now"), "best_r": moment.get("best_r"), "stop_in_force": moment.get("stop"),
        # the stop before this entry (a FADE_TIGHTEN moves it): the report replays "hold" with it
        "stop_before": journal[-2].get("stop") if len(journal) >= 2 else o["stop"],
        "engine_did": ENGINE_DID.get(moment["kind"]), "model": model, "arm": arm,
        **ans,
    }


async def ask(moment, o, journal, model: str, bars=None) -> dict:
    from llm import generate
    prompt = build_prompt(moment, o, journal, bars)
    t0 = time.time()
    try:
        text, model = await generate(model, prompt, temperature=0.1)   # the model that answered (fallback)
        ans = parse_decision(text) or {"decision": None, "confidence": None, "reason": "", "error": "no decision"}
    except Exception as e:                                   # a failed call is recorded, never retried in a loop
        ans = {"decision": None, "confidence": None, "reason": "", "error": f"{type(e).__name__}: {e}"}
    ans["latency_s"] = round(time.time() - t0, 1)
    return record(moment, o, journal, ans, model, "llm")


async def run_forever():
    from llm import SHADOW_MODEL
    from paper.store import EventStore
    from shadow import jev
    # Groq's light model, not the judge's: each Groq model has its own free-tier limits
    # (local model when Groq is out of key or limits -- llm.generate)
    model = SHADOW_MODEL
    jev_key = jev.api_key()
    out_dir = shadow_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    store_dir = EventStore().dir
    logger.info(f"shadow re-analysis watching {store_dir} with {model}"
                f"{' and ' + jev.MODEL if jev_key else ' (Jev arm off: no TYPESAFE_API_KEY)'}; writing {out_dir}")
    last_check: dict = {"llm": {}, "jev": {}}
    reported = None
    while True:
        try:
            now = time.time()
            day = ist_day(now)
            path = store_dir / f"events_{day}.jsonl"
            out = out_dir / f"reanalysis_{day}.jsonl"
            if path.exists():
                events = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
                done = _load_done(out)
                jev_left = jev.JEV_DAILY_CAP - len(done.get("jev", ()))
                # both arms answer a moment back to back with the same bars, so neither sees later prices
                for moment, o, journal, arms in merge_pending(
                        {arm: pending_moments(events, done.get(arm, set()), last_check[arm])
                         for arm in (("jev", "llm") if jev_key else ("llm",))}):
                    bars = await _bars(o.get("token"))
                    for arm in arms:
                        if arm == "jev":
                            if jev_left <= 0:
                                continue
                            jev_left -= 1
                            rec = record(moment, o, journal, await jev.ask_jev(moment, o, journal, bars, jev_key),
                                         jev.MODEL, "jev")
                        else:
                            rec = await ask(moment, o, journal, model, bars)
                        with open(out, "a", encoding="utf-8") as f:
                            f.write(json.dumps(rec) + "\n")
                        logger.info(f"shadow[{arm}]: {o['symbol']} {moment['kind']} -> "
                                    f"{rec.get('decision') or rec.get('error')}")
            # once a day after the close: the scored report
            if hm(now) >= "15:35" and reported != day and out.exists():
                from shadow.report import write_report
                write_report()
                reported = day
        except Exception as e:
            logger.error(f"shadow loop: {e}")
        await asyncio.sleep(POLL_S)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    if os.getenv("QF_SHADOW", "1") == "0":
        logger.info("shadow re-analysis disabled (QF_SHADOW=0); idling")
        while True:
            time.sleep(3600)
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
