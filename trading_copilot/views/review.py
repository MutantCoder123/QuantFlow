"""The Review tab's data (GET /api/review): is the score honest, is it getting
better, what the move after a signal looks like, and where it works.

Every figure comes from signals resolved at the horizon in force
(core.outcome_schema.primary_outcome); a signal graded at a retired horizon
is counted as legacy, never folded in. Thin buckets are marked, not hidden.
"""
from __future__ import annotations

import math

from core.outcome_schema import primary_outcome

MIN_N = 10                    # below this a hit rate is shown as "too few"


def _rate(hits: int, n: int):
    return round(hits / n * 100, 1) if n else None


def ci95(hits: int, n: int):
    """The normal-approximation 95% range of a hit rate, in points, or None."""
    if not n:
        return None
    p = hits / n
    return round(196 * math.sqrt(p * (1 - p) / n), 1)


def measured(signals: list, measure_at=None) -> dict:
    """{rows, pending, legacy}: one row per signal resolved at the primary
    horizon, with what the groups need."""
    rows, pending, legacy = [], 0, 0
    for s in signals or []:
        status = str((s.get("outcome") or {}).get("status", ""))
        if not status.startswith("RESOLVED"):
            pending += 1
            continue
        po = primary_outcome(s, measure_at)
        if po is None:
            legacy += 1
            continue
        snap = s.get("signal_snapshot") or {}
        comp = snap.get("composite_score")
        rows.append({
            "date": s.get("session_date"), "correct": bool(po["directional_correct"]), "pnl": po["pnl_pct"],
            "side": str(snap.get("bias") or "").upper(), "verdict": str(snap.get("verdict") or "").upper(),
            "regime": snap.get("regime"), "phase": snap.get("session_phase"),
            "score": abs(float(comp)) if isinstance(comp, (int, float)) else None,
        })
    return {"rows": rows, "pending": pending, "legacy": legacy}


def trend(rows: list, sessions: int = 20, window: int = 5) -> list:
    """Per session (oldest first): signals resolved, hit rate, and the rolling
    hit rate over the last `window` sessions, weighted by their counts."""
    by_day: dict[str, list] = {}
    for r in rows:
        if r["date"]:
            by_day.setdefault(r["date"], []).append(r)
    days = sorted(by_day)[-sessions:]
    out = []
    for i, d in enumerate(days):
        n = len(by_day[d])
        hits = sum(1 for r in by_day[d] if r["correct"])
        win = [x for dd in days[max(0, i - window + 1):i + 1] for x in by_day[dd]]
        out.append({"date": d, "n": n, "hit_rate": _rate(hits, n), "thin": n < 15,
                    "rolling": _rate(sum(1 for x in win if x["correct"]), len(win))})
    return out


def histogram(rows: list, lo: float = -2.0, hi: float = 2.0, width: float = 0.25) -> dict:
    """The move at the horizon in the signal's direction (%, before costs),
    binned; the ends collect everything beyond them."""
    pnls = sorted(r["pnl"] for r in rows if isinstance(r["pnl"], (int, float)))
    nb = int(round((hi - lo) / width))
    counts = [0] * nb
    for p in pnls:
        i = int((min(max(p, lo), hi - 1e-9) - lo) // width)
        counts[i] += 1
    n = len(pnls)
    median = None
    if n:
        median = round(pnls[n // 2] if n % 2 else (pnls[n // 2 - 1] + pnls[n // 2]) / 2, 3)
    return {"lo": lo, "hi": hi, "width": width,
            "bins": [{"from": round(lo + i * width, 2), "to": round(lo + (i + 1) * width, 2), "n": c} for i, c in enumerate(counts)],
            "n": n, "median": median, "right": sum(1 for p in pnls if p > 0), "wrong": sum(1 for p in pnls if p <= 0)}


_WORDS = {
    "side": {"LONG": "Long", "SHORT": "Short"},
    "verdict": {"CONFIRM": "Confirm", "ADJUST": "Adjust", "DEFER": "Defer", "ABORT": "Reject"},
}


def _label(kind: str, key) -> str:
    if key in (None, "", "UNKNOWN"):
        return "Unknown"
    k = str(key)
    return _WORDS.get(kind, {}).get(k, k.replace("_", " ").capitalize())


def groups(rows: list, min_n: int = MIN_N) -> list:
    """Hit rate by side, AI verdict, regime and session phase; buckets under
    `min_n` are flagged thin (still shown, with their n)."""
    panels = []
    for kind, title in (("side", "By side"), ("verdict", "By AI verdict"), ("regime", "By regime"), ("phase", "By session phase")):
        b: dict[str, list] = {}
        for r in rows:
            b.setdefault(r[kind] or "UNKNOWN", []).append(r)
        items = []
        for k, rs in sorted(b.items(), key=lambda kv: -len(kv[1])):
            hits = sum(1 for x in rs if x["correct"])
            items.append({"label": _label(kind, k), "n": len(rs), "hit_rate": _rate(hits, len(rs)), "thin": len(rs) < min_n})
        panels.append({"title": title, "rows": items})
    return panels


def reliability(rows: list, lo: float = 0.0, width: float = 0.1, n_buckets: int = 10, min_n: int = MIN_N) -> dict:
    """Hit rate per |score| bucket with its 95% range and n; thin buckets carry
    n only. Same buckets as /api/performance/reliability, on this range."""
    buckets = [{"lo": round(lo + i * width, 2), "hi": round(lo + (i + 1) * width, 2), "n": 0, "hits": 0} for i in range(n_buckets)]
    for r in rows:
        if r["score"] is None:
            continue
        i = min(int((r["score"] - lo) / width), n_buckets - 1)
        buckets[i]["n"] += 1
        buckets[i]["hits"] += 1 if r["correct"] else 0
    for b in buckets:
        b["thin"] = b["n"] < min_n
        b["hit_rate"] = None if b["thin"] else _rate(b["hits"], b["n"])
        b["ci"] = None if b["thin"] else ci95(b["hits"], b["n"])
    shown = [b for b in buckets if b["hit_rate"] is not None]
    spread = round(max(b["hit_rate"] for b in shown) - min(b["hit_rate"] for b in shown), 1) if len(shown) >= 2 else None
    return {"buckets": buckets, "shown": len(shown), "spread": spread, "min_n": min_n,
            "first": shown[0]["hit_rate"] if shown else None, "last": shown[-1]["hit_rate"] if shown else None}


def review(signals: list, sessions: int = 20, date: str | None = None, measure_at=None) -> dict:
    m = measured(signals, measure_at)
    rows = [r for r in m["rows"] if (r["date"] == date if date else True)]
    tr = trend(m["rows"], sessions)
    if not date:
        keep = {t["date"] for t in tr}
        rows = [r for r in rows if r["date"] in keep]
    hits = sum(1 for r in rows if r["correct"])
    return {
        "date": date, "sessions": len({r["date"] for r in rows}), "n": len(rows), "hit_rate": _rate(hits, len(rows)),
        "pending": m["pending"], "legacy": m["legacy"], "min_n": MIN_N,
        "reliability": reliability(rows), "trend": tr, "histogram": histogram(rows), "groups": groups(rows),
    }
