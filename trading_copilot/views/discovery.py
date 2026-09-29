"""The Discovery tab's data (GET /api/discovery/view), from the feed process's
job status. Scores are the screener's 0-100 magnitude; signed here by the
side (long up, short down) for the universe chart. Nothing is invented: a
scan that has not run reads as "no scan yet", a missing AI read as missing."""
from __future__ import annotations

from datetime import datetime

AI_TOP = 10          # the AI reads the top 10 (discovery_analysis)


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def _side(bias) -> str:
    return "Long" if str(bias).upper() == "LONG" else "Short"


def _signed(score, bias):
    s = _num(score)
    if s is None:
        return None
    return round((s if str(bias).upper() == "LONG" else -s) / 100.0, 4)


def _secs(a, b):
    try:
        return round((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds())
    except (TypeError, ValueError):
        return None


def _short(e, n=140) -> str:
    s = str(e or "")
    return s if len(s) <= n else s[:n] + "…"


def notes(job: dict) -> list:
    """What the run did, in sentences (the old page's summary line)."""
    out = []
    if job.get("state") == "error":
        return [f"The scan failed: {_short(job.get('error'))}. The watchlist was not changed."]
    if job.get("state") != "done":
        return out
    if not job.get("watchlist_written"):
        out.append("No selection was made, so the watchlist is unchanged.")
    elif job.get("apply_error"):
        out.append(f"The watchlist was updated, but the live update failed ({_short(job['apply_error'])}); "
                   "new picks start at the next restart.")
    else:
        added = len(job.get("live_added") or [])
        pending = len(job.get("subscription_pending") or [])
        out.append(f"The watchlist was updated; {added} {'stock' if added == 1 else 'stocks'} went live"
                   + (f" ({pending} will start streaming when the feed reconnects)." if pending else "."))
    deferred = [r.get("symbol") for r in job.get("removals_deferred") or [] if r.get("symbol")]
    if deferred:
        out.append(f"Leaving the watchlist at the next restart: {', '.join(deferred)}.")
    if job.get("analysis_error"):
        out.append(f"The AI read failed ({_short(job['analysis_error'])}); the scores are unaffected.")
    a = job.get("analysis") or {}
    dev = str(a.get("device") or "")
    if dev == "CPU" or dev.startswith("partial"):
        out.append("The local model did not fully load on the GPU, so the AI read was slow; restarting Ollama "
                   "usually fixes it (after the laptop sleeps, a hybrid-GPU laptop can hide the GPU from it).")
    return out


def view(job: dict | None, watchlist: set) -> dict:
    job = job or {}
    state = job.get("state") or "idle"
    live = {str(t) for t in job.get("live_added") or []}
    analysis = job.get("analysis") or {}
    items = analysis.get("items") or {}
    picks = []
    for i, p in enumerate(job.get("picks") or []):
        sym = str(p.get("symbol"))
        ai = None
        if i < AI_TOP:
            it = items.get(sym)
            if it:
                ai = {"state": "ok", "thesis": it.get("thesis"), "watch_for": it.get("watch_for"),
                      "risks": it.get("risks") or [], "news_alignment": it.get("news_alignment")}
            elif state == "running" and job.get("phase") == "analyzing":
                ai = {"state": "reading"}
            elif job.get("analysis_error"):
                ai = {"state": "error"}
            elif analysis:
                ai = {"state": "missing"}
        picks.append({
            "rank": i + 1, "symbol": sym, "token": str(p.get("token")), "exchange": p.get("exchange"),
            "side": _side(p.get("directional_bias")), "score": _num(p.get("score")),
            "signed": _signed(p.get("score"), p.get("directional_bias")), "rs": _num(p.get("rs")),
            "adv_crore": _num(p.get("adv_crore")), "news": p.get("news") or None, "news_source": p.get("news_source"),
            "on_watchlist": sym.upper() in watchlist, "added_live": str(p.get("token")) in live, "ai": ai,
        })
    universe = None
    if job.get("universe") is not None:
        universe = [{"symbol": u.get("symbol"), "rs": _num(u.get("rs")), "y": _signed(u.get("score"), u.get("directional_bias")),
                     "adv_crore": _num(u.get("adv_crore"))} for u in job["universe"]]
        universe = [u for u in universe if u["rs"] is not None and u["y"] is not None]
    # the score of the last pick the AI reads: the "top-10 cut" line on the chart
    last_read = picks[min(AI_TOP, len(picks)) - 1] if picks else None
    cut = last_read["score"] / 100.0 if last_read and last_read["score"] is not None else None
    return {
        "state": state, "phase": job.get("phase"), "scanned": job.get("scanned") or 0, "total": job.get("total"),
        "started_at": job.get("started_at"), "finished_at": job.get("finished_at"),
        "duration_s": _secs(job.get("started_at"), job.get("finished_at")),
        "picks": picks if job.get("picks") is not None else None, "universe": universe, "cut": cut,
        "ai_model": analysis.get("model"), "notes": notes(job),
    }
