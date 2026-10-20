"""Score the shadow AI re-analysis: had the engine followed the AI at each
moment, would it have made more or less than it did?

For every recorded answer (data/shadow/reanalysis_<day>.jsonl) whose
position has closed:

  EXIT_NOW              close at the price of that moment.
  TIGHTEN_TO_BREAKEVEN  from that moment the stop is breakeven; replayed on
                        the exchange's 1-minute candles to the real close.
  HOLD                  what the engine did -- except at a FADE_TIGHTEN, where
                        the engine tightened: replayed with the stop it had
                        before (to the square-off if the breakeven stop was
                        what closed the trade).

delta = that counterfactual net - the real net (rupees, after costs). Each
answer is scored on its own: a position asked four times counts four times.
Agreement = the AI chose what the engine did. When it disagreed, "right" =
its choice would have made more.

Two arms answer the same moments: the LLM judge model ("llm") and TypeSafe's
Jev ("jev"). Each is scored on its own, then head to head. All three answers
are replayed at every moment, so each record knows which answer was best;
Jev's probabilities are checked against that (calibration).

It recommends nothing by itself; a person reads it.

usage: python trading_copilot/shadow/report.py          (all days; writes the report)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
APP = HERE.parent
for p in (APP, APP / "data_services"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

IST = ZoneInfo("Asia/Kolkata")
SQUARE_OFF = "15:10"


def _hm(ts: float) -> str:
    return datetime.fromtimestamp(ts, IST).strftime("%H:%M")


# ------------------------------------------------------------------ scoring
def _net(side, entry, exit_px, qty, costs_cfg) -> float:
    from paper.fills import pnl, trade_costs
    return pnl(side, entry, exit_px, qty) - trade_costs(side, entry, exit_px, qty, costs_cfg)["total"]


def _replay_stop(side, stop, candles, start_ts, end_ts):
    """First 1-minute candle in [start_ts, end_ts) that touches `stop`:
    (fill, ts) -- the stop, or the open if it gapped through. None if none."""
    for ts, o, h, l, c in candles:
        if ts < start_ts or ts >= end_ts:
            continue
        if (l <= stop) if side == "LONG" else (h >= stop):
            return (min(stop, o) if side == "LONG" else max(stop, o)), ts
    return None


def _close_at(candles, end_ts):
    """The last close before end_ts (the square-off price)."""
    px = None
    for ts, o, h, l, c in candles:
        if ts >= end_ts:
            break
        px = c
    return px


def score_record(rec: dict, o: dict, c: dict, candles: list, costs_cfg: dict) -> dict | None:
    """The record with cf_net / actual_net / delta / agreed, or None if it cannot be scored."""
    dec = rec.get("decision")
    if not dec or c is None:
        return None
    side, entry, qty = o["side"], float(o["entry_price"]), float(o["qty"])
    actual = float(c["net"])
    start = float(rec["moment_ts"]) + 60                        # acted on from the next minute
    cf = actual
    how = "as the engine"
    engine = rec.get("engine_did")
    if dec == "EXIT_NOW":
        px = rec.get("price")
        if px is None:
            return None
        cf, how = _net(side, entry, float(px), qty, costs_cfg), f"exit at {float(px):.2f}"
    elif dec == "TIGHTEN_TO_BREAKEVEN" and engine != "TIGHTEN_TO_BREAKEVEN":
        cur = rec.get("stop_in_force")
        be = entry
        better = cur is None or ((be > float(cur)) if side == "LONG" else (be < float(cur)))
        if better:
            hit = _replay_stop(side, be, candles, start, float(c["ts"]))
            if hit:
                cf, how = _net(side, entry, hit[0], qty, costs_cfg), f"breakeven stop hit {_hm(hit[1])}"
    elif dec == "HOLD" and engine == "TIGHTEN_TO_BREAKEVEN":
        before = rec.get("stop_before")
        if before is not None:
            closed_by_be = c.get("reason") in ("TRAIL_STOP", "STOP") and abs(float(c["exit_price"]) - entry) < 1e-6
            end = float(c["ts"])
            if closed_by_be:
                day = datetime.fromtimestamp(float(c["ts"]), IST)
                h, m = (int(x) for x in SQUARE_OFF.split(":"))
                end = day.replace(hour=h, minute=m, second=0, microsecond=0).timestamp()
            hit = _replay_stop(side, float(before), candles, start, end)
            if hit:
                cf, how = _net(side, entry, hit[0], qty, costs_cfg), f"earlier stop hit {_hm(hit[1])}"
            elif closed_by_be:
                px = _close_at(candles, end)
                if px is not None:
                    cf, how = _net(side, entry, px, qty, costs_cfg), "held to the square-off"
    return dict(rec, actual_net=actual, cf_net=cf, delta=cf - actual, how=how,
                agreed=dec == engine, actual_reason=c.get("reason"))


def option_outcomes(rec: dict, o: dict, c: dict, candles: list, costs_cfg: dict) -> dict:
    """Net rupees had each of the three answers been followed at this moment."""
    from shadow.reanalysis import DECISIONS
    out = {}
    for d in DECISIONS:
        s = score_record(dict(rec, decision=d), o, c, candles, costs_cfg)
        if s:
            out[d] = s["cf_net"]
    return out


def best_options(outcomes: dict, tol: float = 1.0) -> set:
    """The answers that would have made the most (ties within `tol` rupees all count)."""
    if not outcomes:
        return set()
    top = max(outcomes.values())
    return {d for d, v in outcomes.items() if v >= top - tol}


def arm_of(r: dict) -> str:
    return r.get("arm") or "llm"


def head_to_head(scored: list[dict]) -> dict | None:
    """The moments both arms answered: how often they agreed and what each would have made."""
    by = {}
    for r in scored:
        by.setdefault(r.get("review_id"), {})[arm_of(r)] = r
    pairs = [(v["llm"], v["jev"]) for v in by.values() if "llm" in v and "jev" in v]
    if not pairs:
        return None
    return {"n": len(pairs), "same_answer": sum(a["decision"] == b["decision"] for a, b in pairs),
            "llm_delta": sum(a["delta"] for a, _ in pairs), "jev_delta": sum(b["delta"] for _, b in pairs),
            "llm_best": sum(a["decision"] in a.get("best", ()) for a, _ in pairs),
            "jev_best": sum(b["decision"] in b.get("best", ()) for _, b in pairs)}


CAL_BUCKETS = ((0.0, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.01))


def calibration(rows: list[dict]) -> dict | None:
    """For answers carrying probabilities: per band of the chosen answer's
    probability, how often it was (one of) the best answers, plus the Brier
    score of the whole distribution against the best answers (0 = perfect;
    with one best answer a flat 1/3 guess scores 0.67, sure and wrong 2.0)."""
    rows = [r for r in rows if r.get("probabilities") and r.get("best")]
    if not rows:
        return None
    bands = []
    for lo, hi in CAL_BUCKETS:
        b = [r for r in rows if lo <= (r.get("p_chosen") or 0) < hi]
        if b:
            bands.append({"band": f"{lo:.1f}-{min(hi, 1.0):.1f}", "n": len(b),
                          "mean_p": sum(r["p_chosen"] for r in b) / len(b),
                          "best_rate": sum(r["decision"] in r["best"] for r in b) / len(b)})
    brier = 0.0
    for r in rows:
        y = {d: 1.0 / len(r["best"]) if d in r["best"] else 0.0 for d in r["outcomes"]}
        brier += sum((float(r["probabilities"].get(d, 0.0)) - y[d]) ** 2 for d in y)
    return {"n": len(rows), "brier": brier / len(rows), "bands": bands}


def summarize(scored: list[dict]) -> dict:
    def block(rows):
        dis = [r for r in rows if not r["agreed"]]
        return {"n": len(rows), "agreed": sum(r["agreed"] for r in rows),
                "delta": sum(r["delta"] for r in rows),
                "disagreed": len(dis), "right": sum(r["delta"] > 0 for r in dis),
                "wrong": sum(r["delta"] < 0 for r in dis)}
    out = {"all": block(scored), "by_decision": {}, "by_moment": {}}
    for key, field in (("by_decision", "decision"), ("by_moment", "moment")):
        for v in sorted({r[field] for r in scored}):
            out[key][v] = block([r for r in scored if r[field] == v])
    return out


# ------------------------------------------------------------------ data
def _candles(symbol: str, day: str, cache: Path) -> list:
    """Exchange 1-minute candles [(ts, o, h, l, c)], cached once the day is complete."""
    import requests
    f = cache / f"{symbol}_{day}.json"
    if f.exists():
        return [tuple(x) for x in json.loads(f.read_text())]
    import scrip_master_engine
    from paths import BASE_DIR
    tok = json.loads((BASE_DIR / "upstox_token.json").read_text())["access_token"]
    key = scrip_master_engine.get_instrument_key(symbol)
    today = datetime.now(IST).strftime("%Y-%m-%d")
    url = (f"https://api.upstox.com/v3/historical-candle/intraday/{key}/minutes/1" if day == today
           else f"https://api.upstox.com/v3/historical-candle/{key}/minutes/1/{day}/{day}")
    raw = requests.get(url, headers={"Authorization": f"Bearer {tok}", "Accept": "application/json"},
                       timeout=20).json()["data"]["candles"]
    c = sorted((datetime.fromisoformat(x[0]).timestamp(), x[1], x[2], x[3], x[4]) for x in raw)
    if c and _hm(c[-1][0]) >= "15:29":
        cache.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(c))
    return c


def load_scored(shadow: Path | None = None) -> list[dict]:
    from core.costs import load_costs
    from paper.store import EventStore
    from shadow.reanalysis import shadow_dir
    shadow = shadow or shadow_dir()
    events = EventStore().load()
    opens = {e["pos_id"]: e for e in events if e.get("type") == "OPEN"}
    closes = {e["pos_id"]: e for e in events if e.get("type") == "CLOSE"}
    costs = load_costs()
    scored = []
    for path in sorted(shadow.glob("reanalysis_*.jsonl")):
        day = path.stem.split("_")[1]
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            o, c = opens.get(rec.get("pos_id")), closes.get(rec.get("pos_id"))
            if not o or not c or not rec.get("decision"):
                continue
            try:
                candles = _candles(o["symbol"], day, shadow / "cache")
            except Exception:
                candles = []
            s = score_record(rec, o, c, candles, costs)
            if s:
                s["day"] = day
                s["outcomes"] = option_outcomes(rec, o, c, candles, costs)
                s["best"] = sorted(best_options(s["outcomes"]))
                scored.append(s)
    return scored


ARM_NAMES = {"llm": "LLM judge model", "jev": "Jev (TypeSafe)"}


def _arm_section(rows: list[dict], title: str) -> list[str]:
    s = summarize(rows)
    a = s["all"]
    verdict = "would have added" if a["delta"] > 0 else "would have cost"
    best = sum(r["decision"] in r.get("best", ()) for r in rows)
    lines = [
        f"## {title}", "",
        f"**Following it at every moment {verdict} ₹{abs(a['delta']):,.0f}** in total "
        f"({a['agreed']} of {a['n']} answers agreed with the engine; of {a['disagreed']} disagreements, "
        f"{a['right']} would have made more and {a['wrong']} less). "
        f"It picked the best of the three answers {best} of {a['n']} times.", "",
        "### By answer", "", "| Answer | n | Agreed | Disagreed: right / wrong | Δ net |", "|---|---|---|---|---|",
    ]
    for k, b in s["by_decision"].items():
        lines.append(f"| {k} | {b['n']} | {b['agreed']} | {b['right']} / {b['wrong']} | ₹{b['delta']:+,.0f} |")
    lines += ["", "### By moment", "", "| Moment | n | Agreed | Disagreed: right / wrong | Δ net |", "|---|---|---|---|---|"]
    for k, b in s["by_moment"].items():
        lines.append(f"| {k} | {b['n']} | {b['agreed']} | {b['right']} / {b['wrong']} | ₹{b['delta']:+,.0f} |")
    cal = calibration(rows)
    if cal:
        lines += ["", "### Are its probabilities honest?", "",
                  f"Brier score {cal['brier']:.2f} over {cal['n']} answers (0 is perfect; a flat one-in-three "
                  "guess scores 0.67). In each band, the share of answers that were the best should be close "
                  "to the probability it gave.", "",
                  "| Probability of its answer | n | Mean probability | Was the best |", "|---|---|---|---|"]
        for b in cal["bands"]:
            lines.append(f"| {b['band']} | {b['n']} | {b['mean_p']:.2f} | {b['best_rate']:.0%} |")
    return lines + [""]


def to_markdown(scored: list[dict], now: float | None = None) -> str:
    days = sorted({r["day"] for r in scored})
    when = datetime.fromtimestamp(now, IST).strftime("%Y-%m-%d %H:%M") if now else ""
    lines = [
        "# Shadow AI re-analysis: would following the AI have helped?", "",
        f"Generated {when} IST · {len(days)} session(s) · {len(scored)} scored answers", "",
        "> Shadow only: the AI was asked at each key moment of a paper position and never acted. "
        "Each answer is scored on its own against what the engine actually did.", "",
    ]
    if not scored:
        return "\n".join(lines + ["_No scored answers yet (needs closed positions with recorded answers)._", ""])
    arms = sorted({arm_of(r) for r in scored})
    for arm in arms:
        lines += _arm_section([r for r in scored if arm_of(r) == arm], ARM_NAMES.get(arm, arm))
    h = head_to_head(scored)
    if h:
        lines += ["## Head to head", "",
                  f"On the {h['n']} moments both answered, they gave the same answer {h['same_answer']} times. "
                  f"Following the LLM: ₹{h['llm_delta']:+,.0f}; following Jev: ₹{h['jev_delta']:+,.0f}. "
                  f"Best of the three answers: LLM {h['llm_best']}, Jev {h['jev_best']}.", ""]
    lines += ["## Every disagreement with the engine", "",
              "| Day | Stock | Moment | Engine | Arm | Answer | Reason / probabilities | Outcome | Δ net |",
              "|---|---|---|---|---|---|---|---|---|"]
    for r in sorted((r for r in scored if not r["agreed"]), key=lambda r: (r["day"], r["moment_ts"], arm_of(r))):
        reason = (r.get("reason") or "").replace("|", "/")[:120]
        lines.append(f"| {r['day'][5:]} {_hm(r['moment_ts'])} | {r['symbol']} | {r['moment']} | {r['engine_did']} | "
                     f"{arm_of(r)} | {r['decision']} | {reason} | {r['how']} | ₹{r['delta']:+,.0f} |")
    lines += ["", "Read this after 10+ sessions: a few answers prove nothing either way.", ""]
    return "\n".join(lines)


def write_report(now: float | None = None) -> Path:
    import time
    from shadow.reanalysis import shadow_dir
    now = now or time.time()
    scored = load_scored()
    out = shadow_dir() / "reports"
    out.mkdir(parents=True, exist_ok=True)
    day = datetime.fromtimestamp(now, IST).strftime("%Y-%m-%d")
    md = out / f"reanalysis_{day}.md"
    md.write_text(to_markdown(scored, now), encoding="utf-8")
    arms = sorted({arm_of(r) for r in scored})
    summary = {arm: dict(summarize([r for r in scored if arm_of(r) == arm]),
                         calibration=calibration([r for r in scored if arm_of(r) == arm])) for arm in arms}
    (out / f"reanalysis_{day}.json").write_text(json.dumps({"summary": summary, "head_to_head": head_to_head(scored),
                                                            "scored": scored},
                                                           default=float, indent=1), encoding="utf-8")
    return md


if __name__ == "__main__":
    path = write_report()
    print(path.read_text(encoding="utf-8"))
    print(f"\nSaved to {path}")
