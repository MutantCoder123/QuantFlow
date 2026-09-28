"""The evidence report: what the paper account says should be looked at,
each finding with its sample size and the config keys it points to.

It RECOMMENDS ONLY. Nothing here -- or anywhere that calls it -- writes
configuration; a person reads the report and decides. Pure: builds a dict
from a Scope, and renders that dict as Markdown.

Every rate it quotes comes from a gated metric whose status is "ok"; a
segment below its minimum sample is never named as weak or strong.
"""
from __future__ import annotations

from datetime import datetime
from statistics import mean

from performance.breakdowns import DIMENSIONS, breakdown
from performance.diagnostics import EXIT_TUNES, all_diagnostics
from performance.metrics import daily, headline, risk_adjusted
from performance.scope import IST, Scope, ist_day

DISCLAIMER = ("This report recommends only. Nothing in the system changes configuration from it; "
              "a person reads it and decides.")

# segment dimension -> (how to name a bucket, the keys it points to)
SEGMENTS = {
    "regime_side": ["gates.regime_dampening", "conviction.bias_threshold"],
    "symbol": ["watchlist_policy.core", "watchlist_policy.min_adv_crore"],
    "cluster": ["risk.max_cluster_risk_pct", "watchlist_policy.cluster_cap"],
    "hour": ["horizon.entry_cutoff_ist", "gates.failure_to_launch_min"],
    "session_phase": ["horizon.entry_cutoff_ist", "gates.regime_dampening"],
    "score_bucket": ["gates.min_stat_edge", "conviction.bias_threshold"],
    "verdict": ["paper.judge_model"],
}
EXIT_LABEL = {"GATEKEEPER_STOP_PROXIMITY": "Stop proximity", "GATEKEEPER_WHALE_FLIP": "Whale flip",
              "GATEKEEPER_UNSPECIFIED": "Gatekeeper", "LLM_CLOSE": "AI close", "LLM_REVERSE": "AI reverse",
              "SQUARE_OFF": "Square-off", "RECOVERED_STALE": "Closed after restart"}
TREND_WINDOW = 5                     # sessions per side of the session-over-session comparison


# --------------------------------------------------------------- formatting
def inr(v: float, signed: bool = True) -> str:
    """Rupees with Indian digit grouping: +₹10,12,480."""
    n = round(abs(v))
    s = str(n)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    sign = "−" if v < 0 and n else ("+" if signed and v > 0 and n else "")
    return f"{sign}₹{s}"


def rr(v: float) -> str:
    return f"{'−' if v < 0 else '+'}{abs(v):.2f} R" if round(v, 2) else "0.00 R"


def _ok(m):
    return m["value"] if m and m.get("status") == "ok" else None


def _sentence(code: str) -> str:
    s = str(code).replace("_", " ").lower()
    return s[:1].upper() + s[1:]


def segment_label(dim: str, key) -> str:
    if dim == "regime_side":
        side, regime = str(key).split("|", 1)
        return f"{'Longs' if side == 'LONG' else 'Shorts'} in {_sentence(regime).lower()}"
    if dim == "hour":
        h = int(str(key)[:2])
        return f"Entries {h:02d}:00–{h + 1:02d}:00"
    if dim == "score_bucket":
        return f"Score {key}"
    if dim == "verdict":
        return f"Trades the AI marked {key}"
    if dim == "session_phase":
        return _sentence(key)
    return str(key)


def trades_n(n: int) -> str:
    return f"{n} trade" if n == 1 else f"{n} trades"


def _item(text: str, n: int | None = None, tunes: list | None = None) -> dict:
    return {"text": text, "n": n, "tunes": list(dict.fromkeys(tunes or []))}


# ----------------------------------------------------------------- sections
def weakest_segments(trades: list, min_bucket: int, top: int = 3) -> dict:
    """Segments with enough trades whose average trade loses, most rupees lost first."""
    weak = []
    for dim, tunes in SEGMENTS.items():
        members: dict = {}
        for t in trades:
            members.setdefault(DIMENSIONS[dim](t), set()).add(t["pos_id"])
        for row in breakdown(trades, dim, min_bucket):
            er = _ok(row["expectancy_r"])
            if row["key"] is None or er is None or er >= 0:
                continue
            weak.append((row["net"]["value"], dim, row, er, tunes, frozenset(members[row["key"]])))
    weak.sort(key=lambda x: x[0])
    picked, seen = [], set()
    for w in weak:                   # the same trades seen as a regime, a stock and a cluster count once
        if w[5] in seen:
            continue
        seen.add(w[5])
        picked.append(w)
        if len(picked) == top:
            break
    items = [_item(f"{segment_label(dim, row['key'])}: average {rr(er)} across {trades_n(row['n'])}, "
                   f"{inr(net)} net.", row["n"], tunes)
             for net, dim, row, er, tunes, _ in picked]
    return {"id": "weakest", "title": "Weakest segments", "items": items,
            "empty": f"No segment with {min_bucket} or more trades has a losing average trade."}


def losing_exit_rules(diag: dict, min_bucket: int) -> dict:
    """Rule-based exits (not the planned stop/target) that lose money overall."""
    rows = [r for r in diag["exits"]["rows"]
            if r["key"] not in ("STOP", "TARGET") and r["n"] >= min_bucket and r["net"]["value"] < 0]
    rows.sort(key=lambda r: r["net"]["value"])
    items = [_item(f"{EXIT_LABEL.get(r['key'], _sentence(r['key']))} exits: {inr(r['net']['value'])} across "
                   f"{trades_n(r['n'])}" + (f", average {rr(_ok(r['expectancy_r']))}" if _ok(r["expectancy_r"]) is not None else "")
                   + ".", r["n"], EXIT_TUNES.get(r["key"], []))
             for r in rows]
    return {"id": "exits", "title": "Exit rules that cost money", "items": items,
            "empty": f"No rule-based exit with {min_bucket} or more trades loses money overall."}


def calibration_gaps(diag: dict) -> dict:
    c = diag["calibration"]
    ok = [b for b in c["buckets"] if b["bucket"] and _ok(b["win_rate"]) is not None]
    items = []
    for lo, hi in zip(ok, ok[1:]):
        if _ok(hi["win_rate"]) < _ok(lo["win_rate"]):
            items.append(_item(f"Score {hi['bucket']} wins {_ok(hi['win_rate']):.0f}% of {hi['n']} trades, "
                               f"less than score {lo['bucket']} at {_ok(lo['win_rate']):.0f}% of {lo['n']}: "
                               "a higher score is not winning more here.", hi["n"] + lo["n"], c["tunes"]))
    empty = ("Win rate rises with the score in every bucket that has enough trades."
             if len(ok) >= 2 else "Fewer than two score buckets have enough trades to compare.")
    return {"id": "calibration", "title": "Calibration gaps", "items": items, "empty": empty}


def stops_and_costs(diag: dict, head: dict) -> dict:
    items = []
    e = diag["excursions"]
    up = _ok(e["stops_after_half_r_up_pct"])
    if up is not None and up >= 40:
        items.append(_item(f"{up:.0f}% of {e['stops_after_half_r_up_pct']['n']} stopped trades were up +0.50 R or "
                           "more first: profit is being handed back before the stop.",
                           e["stops_after_half_r_up_pct"]["n"], e["tunes"]))
    gb = _ok(e["avg_given_back_r"])
    if gb is not None and gb >= 0.5:
        items.append(_item(f"The average trade gave back {gb:.2f} R from its best point before closing.",
                           e["avg_given_back_r"]["n"], ["conviction.min_reward_risk", "gates.stop_proximity_pct"]))
    drag = _ok(head["cost_drag_pct"])
    if drag is not None and drag >= 25:
        items.append(_item(f"Costs took {drag:.1f}% of the gross winnings.", head["cost_drag_pct"]["n"],
                           diag["costs"]["tunes"]))
    return {"id": "stops_costs", "title": "Stops, targets and costs", "items": items,
            "empty": "Nothing here crosses its threshold yet (stops handed back ≥ 40%, "
                     "give-back ≥ 0.50 R, cost drag ≥ 25%), or the samples are too small."}


def config_versions(trades: list, min_rates: int) -> dict:
    """Did a policy change help? Performance per config_version."""
    rows = [r for r in breakdown(trades, "config_version", min_rates) if r["key"] is not None]
    items = [_item(f"Policy v{r['key']}: {trades_n(r['n'])}, {inr(r['net']['value'])} net"
                   + (f", average {rr(_ok(r['expectancy_r']))}" if _ok(r["expectancy_r"]) is not None
                      else f" (average R needs {min_rates} trades)") + ".", r["n"], [])
             for r in rows]
    verdict = None
    ok = [r for r in rows if _ok(r["expectancy_r"]) is not None]
    if len(ok) >= 2:
        prev, last = ok[-2], ok[-1]
        d = _ok(last["expectancy_r"]) - _ok(prev["expectancy_r"])
        verdict = (f"Policy v{last['key']} averages {rr(_ok(last['expectancy_r']))} per trade against "
                   f"{rr(_ok(prev['expectancy_r']))} for v{prev['key']}: "
                   + ("better" if d > 0 else "worse" if d < 0 else "no different") + f" by {abs(d):.2f} R.")
    note = ("" if verdict else "Only one policy version has traded in this range; the comparison starts "
            "after a change." if len(rows) <= 1 else f"No two versions have {min_rates} trades each yet.")
    return {"id": "versions", "title": "Did my change help?", "items": items, "verdict": verdict,
            "note": note, "empty": note}


def session_trend(sc: Scope, min_rates: int) -> dict:
    """The last TREND_WINDOW sessions against the TREND_WINDOW before them."""
    rows = daily(sc)
    need = 2 * TREND_WINDOW
    if len(rows) < need:
        return {"id": "trend", "title": "Session over session", "items": [],
                "empty": f"Needs {need} sessions to compare the last {TREND_WINDOW} with the {TREND_WINDOW} before; "
                         f"have {len(rows)}."}
    last, prev = rows[-TREND_WINDOW:], rows[-need:-TREND_WINDOW]

    def window(ws):
        days = {w["day"] for w in ws}
        ts = [t for t in sc.trades if ist_day(t["closed_ts"]) in days]
        rs = [t["r_net"] for t in ts if t.get("r_net") is not None]
        return {"net": mean(w["net"] for w in ws), "trades": sum(w["trades"] for w in ws),
                "r": mean(rs) if len(rs) >= min_rates else None, "n": len(rs),
                "from": ws[0]["day"], "to": ws[-1]["day"]}

    a, b = window(prev), window(last)
    items = [_item(f"Last {TREND_WINDOW} sessions ({b['from']} to {b['to']}): {inr(b['net'])} a session, "
                   f"{b['trades']} trades; the {TREND_WINDOW} before: {inr(a['net'])} a session, {a['trades']} trades.",
                   a["trades"] + b["trades"])]
    if a["r"] is not None and b["r"] is not None:
        items.append(_item(f"Average trade moved from {rr(a['r'])} to {rr(b['r'])}.", a["n"] + b["n"]))
    else:
        items.append(_item(f"Average R per window needs {min_rates} trades on each side "
                           f"(have {a['n']} and {b['n']}).", a["n"] + b["n"]))
    return {"id": "trend", "title": "Session over session", "items": items, "empty": ""}


def not_yet(head: dict, risk: dict) -> list[str]:
    names = {"win_rate": "Win rate", "profit_factor": "Profit factor", "expectancy_r": "Average trade (R)",
             "sharpe": "Sharpe", "sortino": "Sortino", "calmar": "Calmar"}
    out = []
    for k, label in names.items():
        m = head.get(k) or risk.get(k)
        if m and m["status"] == "insufficient":
            unit = "sessions" if k in ("sharpe", "sortino", "calmar") else "trades"
            out.append(f"{label} needs {m['min_n']} {unit} (have {m['n']}).")
    return out


# ------------------------------------------------------------------- build
def build(sc: Scope, arms: list, min_n: dict, now: float) -> dict:
    min_bucket = int(min_n.get("bucket", 10))
    min_rates = int(min_n.get("rates", 20))
    head = headline(sc, min_n)
    risk = risk_adjusted(sc, min_n)
    diag = all_diagnostics(sc.trades, sc.rejections, arms, min_n)
    sections = [
        weakest_segments(sc.trades, min_bucket),
        losing_exit_rules(diag, min_bucket),
        calibration_gaps(diag),
        stops_and_costs(diag, head),
        config_versions(sc.trades, min_rates),
        session_trend(sc, min_rates),
    ]
    return {
        "generated_at": now,
        "generated_ist": datetime.fromtimestamp(now, IST).strftime("%Y-%m-%d %H:%M IST"),
        "range": {"first_day": sc.first_day, "last_day": sc.last_day},
        "sessions": len(sc.session_days), "trades": head["trades"]["value"],
        "net": head["net_pnl"]["value"], "expectancy_r": _ok(head["expectancy_r"]),
        "settings_changes": [{"day": ist_day(e["ts"]), "old": e.get("old"), "new": e.get("new")}
                             for e in sc.settings_changes if sc.contains(e["ts"])],
        "sections": sections, "not_yet": not_yet(head, risk), "disclaimer": DISCLAIMER,
    }


def to_markdown(rep: dict) -> str:
    rg = rep["range"]
    span = "all time" if not rg["first_day"] else f"{rg['first_day']} to {rg['last_day']}"
    lines = [f"# Paper account evidence report", "",
             f"Generated {rep['generated_ist']} · {span} · {rep['sessions']} sessions · {rep['trades']} trades · "
             f"{inr(rep['net'])} net" + (f" · average trade {rr(rep['expectancy_r'])}" if rep["expectancy_r"] is not None else ""),
             "", f"> {rep['disclaimer']}", ""]
    for s in rep["sections"]:
        lines += [f"## {s['title']}", ""]
        if s.get("verdict"):
            lines += [f"**{s['verdict']}**", ""]
        for it in s["items"]:
            tunes = f" — tunes {', '.join(f'`{k}`' for k in it['tunes'])}" if it["tunes"] else ""
            lines.append(f"- {it['text']}{tunes}")
        if not s["items"] and s.get("empty"):
            lines.append(f"_{s['empty']}_")
        elif s["items"] and s.get("note"):
            lines += ["", f"_{s['note']}_"]
        lines.append("")
    if rep["settings_changes"]:
        lines += ["## Settings changes in this range", ""]
        for c in rep["settings_changes"]:
            ch = ", ".join(f"{k} {(c['old'] or {}).get(k)} → {v}" for k, v in (c["new"] or {}).items())
            lines.append(f"- {c['day']}: {ch}")
        lines.append("")
    if rep["not_yet"]:
        lines += ["## Not enough data yet", ""] + [f"- {x}" for x in rep["not_yet"]] + [""]
    return "\n".join(lines)
