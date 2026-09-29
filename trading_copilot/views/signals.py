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


# ------------------------------------------------------------------ the funnel
RUN_GAP_S = 60            # a gap longer than this between authorised checks starts a new idea
REVIEW_GRACE_S = 180      # the AI's answer (and the paper fill) can land after the run ends
ENTRIES = ("EXECUTE_LONG", "EXECUTE_SHORT")
TAKEN = ("CONFIRM", "ADJUST")


def _gate_word(code: str) -> str:
    c = str(code or "UNKNOWN")
    if c == "UNKNOWN":
        return "Held by the gatekeeper"
    if c.startswith("REGIME_DAMPENED_"):
        return "Held back by the session phase"
    if c.startswith("STALE_DATA"):
        return "Stale price"
    if c.startswith("ENTRY_CUTOFF"):
        return "After the entry cutoff"
    return c.replace("_", " ").capitalize()


def idea_runs(records: list) -> list:
    """Group feature-log records into ideas: per symbol, consecutive checks where
    the math found a setup (PROPOSED or GATED_*), split by a gap > RUN_GAP_S.
    records: [{ts, symbol, decision}] in any order."""
    by_sym: dict[str, list] = {}
    for r in records or []:
        d = str(r.get("decision") or "")
        if d == "PROPOSED" or d.startswith("GATED_"):
            by_sym.setdefault(str(r.get("symbol")), []).append((int(r["ts"]), d))
    runs = []
    for sym, rows in by_sym.items():
        rows.sort()
        cur = None
        for ts, d in rows:
            if cur is None or ts - cur["end"] > RUN_GAP_S:
                cur = {"symbol": sym, "start": ts, "end": ts, "proposed": False, "gates": {}}
                runs.append(cur)
            cur["end"] = ts
            if d == "PROPOSED":
                cur["proposed"] = True
            else:
                g = d[len("GATED_"):]
                cur["gates"][g] = cur["gates"].get(g, 0) + 1
    return sorted(runs, key=lambda r: (r["start"], r["symbol"]))


def _in(run, ts) -> bool:
    return run["start"] <= ts <= run["end"] + REVIEW_GRACE_S


def _bump(d: dict, k: str) -> None:
    d[k] = d.get(k, 0) + 1


def funnel(records: list, arms: list, events: list) -> dict:
    """Where today's ideas stop: how many reach each stage and, per stage, why
    the rest stopped there. Built only from the day's logs (feature log, arms
    journal, paper events); a stage with nothing logged is 0, never guessed.
    The AI is called many times per idea, so an idea counts once per stage."""
    runs = idea_runs(records)
    arm_rows = sorted(((int(a.get("ts") or 0), a) for a in arms or []), key=lambda x: x[0])
    ev = [e for e in events or [] if e.get("type") in ("OPEN", "REJECT")]

    stages = {"found": len(runs), "cleared": 0, "reviewed": 0, "confirmed": 0, "opened": 0}
    drops = {"cleared": {}, "reviewed": {}, "confirmed": {}, "opened": {}}
    for run in runs:
        if not run["proposed"]:
            top = max(run["gates"].items(), key=lambda kv: kv[1])[0] if run["gates"] else "UNKNOWN"
            _bump(drops["cleared"], _gate_word(top))
            continue
        stages["cleared"] += 1
        reviewed = [a for ts, a in arm_rows if a.get("symbol") == run["symbol"] and _in(run, ts)]
        if not reviewed:
            _bump(drops["reviewed"], "Not sent to the AI")
            continue
        stages["reviewed"] += 1
        taken = [a for a in reviewed if str((a.get("llm_arm") or {}).get("verdict")).upper() in TAKEN
                 and str((a.get("llm_arm") or {}).get("action")).upper() in ENTRIES]
        if not taken:
            last = reviewed[-1].get("llm_arm") or {}
            v, act = str(last.get("verdict", "")).upper(), str(last.get("action", "")).upper()
            if v == "ABORT":
                why = "AI rejected"
            elif v == "DEFER":
                why = "AI deferred"
            elif act == "PASS":
                why = "AI passed"
            elif act in ("CLOSE_EXISTING", "REVERSE_POSITION", "HOLD"):
                why = "AI managed a position"
            else:
                why = "AI said no"
            _bump(drops["confirmed"], why)
            continue
        stages["confirmed"] += 1
        mine = [e for e in ev if e.get("symbol") == run["symbol"] and _in(run, float(e.get("ts") or 0))]
        if any(e["type"] == "OPEN" for e in mine):
            stages["opened"] += 1
            continue
        rejects = [e for e in mine if e["type"] == "REJECT"]
        _bump(drops["opened"], (rejects[-1].get("reason") or "UNKNOWN") if rejects else "NO_PAPER_EVENT")
    return {"stages": stages, "drops": drops, "ai_calls": len(arm_rows)}


def timeline(arms: list, events: list, now: float) -> list:
    """Per stock, today: the AI's verdicts as marks and the paper holds as bars.
    [{symbol, marks: [{ts, kind}], holds: [{start, end, kind}]}], busiest first."""
    lanes: dict[str, dict] = {}

    def lane(sym):
        return lanes.setdefault(sym, {"symbol": sym, "marks": [], "holds": []})

    for a in arms or []:
        llm = a.get("llm_arm") or {}
        v, act = str(llm.get("verdict", "")).upper(), str(llm.get("action", "")).upper()
        if v in TAKEN and act in ENTRIES:
            kind = "taken"
        elif v == "ABORT":
            kind = "rejected"
        elif v == "DEFER":
            kind = "deferred"
        else:
            kind = "other"
        lane(str(a.get("symbol")))["marks"].append({"ts": int(a.get("ts") or 0), "kind": kind})
    opens = {}
    for e in sorted(events or [], key=lambda e: e.get("ts") or 0):
        if e.get("type") == "OPEN":
            opens[e.get("pos_id")] = e
        elif e.get("type") == "CLOSE" and e.get("pos_id") in opens:
            o = opens.pop(e["pos_id"])
            lane(str(o.get("symbol")))["holds"].append(
                {"start": o["ts"], "end": e["ts"], "kind": "win" if (e.get("net") or 0) > 0 else "loss"})
    for o in opens.values():
        lane(str(o.get("symbol")))["holds"].append({"start": o["ts"], "end": now, "kind": "open"})
    return sorted(lanes.values(), key=lambda l: (-(len(l["marks"]) + 3 * len(l["holds"])), l["symbol"]))


# ------------------------------------------------------------------- the queue
def _stage(tone: str, text: str) -> dict:
    return {"tone": tone, "text": text}


def queue_row(symbol: str, sp: dict | None, report: dict | None, paper: dict | None,
              reject: dict | None, rank: float | None) -> dict | None:
    """One stock's idea and where it stands: math -> risk -> AI -> paper, each
    stage {tone: ok|bad|wait|off, text}. None when the stock has no idea at all
    (the math found nothing, the AI said nothing, nothing is held)."""
    math = (sp or {}).get("math_setup") or {}
    regime = ((sp or {}).get("market_regime") or {}).get("current_regime")
    rep = report or {}
    has_setup = bool(math) and not math.get("setup_rejected", True)
    status = rep.get("Status_Tag") or ""
    if not (has_setup or paper or status or rep.get("llm_authorized")):
        return None
    bias = str(math.get("directional_bias") or "").upper()
    geo = math.get("execution_geometry") or {}
    exp = math.get("expectancy_matrix") or {}

    if has_setup:
        m = _stage("ok", f"{bias.capitalize()} {abs(float(math.get('composite_score') or 0)):.2f}")
    else:
        m = _stage("off", "Holding" if paper else "No new setup")

    rr = rep.get("Risk_Rejection")
    if rr and rr != "NO_GEOMETRY":
        risk = _stage("bad", rr)
    elif (rep.get("Qty") or 0) > 0:
        risk = _stage("ok", f"Sized {int(rep['Qty'])}")
    else:
        risk = _stage("off", "")

    action = str(rep.get("Action") or "").upper()
    if status == "LLM_ANALYZED":
        ai = _stage("ok" if action in ("LONG", "SHORT") else "off", f"AI: {action.capitalize() or 'Wait'}")
    elif status == "PENDING_LLM":
        ai = _stage("wait", "AI reviewing")
    elif status == "RANK_GATED":
        ai = _stage("off", "Below the cut")
    elif status == "REQUIRED LLM ANALYZE":
        ai = _stage("wait", "AI review off")
    elif status == "STABILIZING":
        ai = _stage("wait", "Settling")
    else:
        ai = _stage("off", "")

    if paper:
        p = _stage("ok", "Open")
    elif reject:
        p = _stage("bad", str(reject.get("reason") or "Rejected"))
    else:
        p = _stage("off", "")

    return {
        "symbol": symbol, "bias": bias if bias in ("LONG", "SHORT") else None,
        "composite": float(math["composite_score"]) if has_setup and math.get("composite_score") is not None else None,
        "regime": regime, "rank": rank, "pipeline": [m, risk, ai, p],
        "entry": geo.get("calculated_entry"), "stop": geo.get("padded_stop"), "target": geo.get("calculated_target"),
        "rr": exp.get("reward_risk"), "reason": rep.get("Reason") if status == "LLM_ANALYZED" else None,
        "time": rep.get("Generated_Time") if rep.get("Generated_Time") not in (None, "UNKNOWN") else None,
        "paper": {"side": paper.get("side"), "entry": paper.get("entry_price"), "stop": paper.get("stop"),
                  "target": paper.get("target"), "last": paper.get("last"), "r_now": paper.get("r_now"),
                  "opened": paper.get("ts")} if paper else None,
        "wants_action": status in ("PENDING_LLM", "REQUIRED LLM ANALYZE")
        or (status == "LLM_ANALYZED" and action in ("LONG", "SHORT")),
        "blocked": risk["tone"] == "bad" or p["tone"] == "bad",
    }


def order_queue(rows: list) -> list:
    """By attention: held first, then the attention rank (the engine's EV x
    freshness), then the size of the score. A missing rank sorts last."""
    def key(r):
        rank = r.get("rank")
        return (0 if r.get("paper") else 1,
                -(rank if isinstance(rank, (int, float)) else float("-inf")),
                -abs(r.get("composite") or 0))
    return sorted(rows, key=key)
