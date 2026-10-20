"""Does the AI judge help? Every recorded escalation, whatever the verdict.

Each escalation is journalled as an ArmRecord (data/signals/arms/arms_<day>.jsonl):
the math arm's proposed trade (side, entry, stop, target) and the AI's verdict
(CONFIRM / ADJUST / DEFER / ABORT). Each proposal is traded on the exchange's
1-minute candles from the next minute -- stop, target or 15:10 -- and scored
in R after friction (0.072% of the position value). If the trades the AI
waved through do no better than the ones it held back, the judge adds latency,
not edge.

Escalations repeat (the same stock, minutes apart): the first per stock, side
and 30 minutes is kept ("deduped") beside the full count.

usage: python trading_copilot/shadow/judge_report.py      (prints; writes data/shadow/reports/judge_<date>.md)
"""
from __future__ import annotations

import glob
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
APP = HERE.parent
for p in (APP, APP / "data_services", APP / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

IST = ZoneInfo("Asia/Kolkata")
FRICTION = 0.00072
TAKEN = ("CONFIRM", "ADJUST")


def outcome_r(m: pd.DataFrame, ts: float, side: str, entry: float, stop: float, target: float) -> float | None:
    t0 = pd.Timestamp(ts, unit="s", tz=IST).tz_localize(None)
    after = m[(m.ts > t0) & (m.ts.dt.strftime("%H:%M") < "15:10")]
    if after.empty or not stop or not target or not entry:
        return None
    d = abs(entry - stop)
    if d <= 0:
        return None
    sgn = 1 if side == "LONG" else -1
    adv = (entry - after.l.values) if sgn > 0 else (after.h.values - entry)
    fav = (after.h.values - entry) if sgn > 0 else (entry - after.l.values)
    td = abs(target - entry)
    s_hit = np.argmax(adv >= d) if (adv >= d).any() else None
    t_hit = np.argmax(fav >= td) if (fav >= td).any() else None
    if s_hit is not None and (t_hit is None or s_hit <= t_hit):
        r = -1.0
    elif t_hit is not None:
        r = td / d
    else:
        r = sgn * (float(after.c.iloc[-1]) - entry) / d
    return r - FRICTION * entry / d


def load() -> pd.DataFrame:
    from fit_ev_calibration import minutes
    from paths import SIGNALS_DIR
    recs = []
    for f in sorted(glob.glob(str(SIGNALS_DIR / "arms" / "arms_*.jsonl"))):
        for line in open(f, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            ma, la = r.get("math_arm") or {}, r.get("llm_arm") or {}
            side = str(ma.get("action", "")).upper()
            if side not in ("LONG", "SHORT"):
                continue
            recs.append({"ts": r["ts"], "sym": r["symbol"], "side": side, "verdict": la.get("verdict", "UNKNOWN"),
                         "entry": ma.get("entry"), "stop": ma.get("stop"), "target": ma.get("target"),
                         "composite": ma.get("composite")})
    d = pd.DataFrame(recs)
    if d.empty:
        return d
    d["day"] = pd.to_datetime(d.ts, unit="s", utc=True).dt.tz_convert(IST).dt.strftime("%Y-%m-%d")
    out = []
    cache = {}
    for (sym, day), g in d.groupby(["sym", "day"]):
        if (sym, day) not in cache:
            cache[(sym, day)] = minutes(sym, day)
        m = cache[(sym, day)]
        if m is None:
            continue
        for r in g.itertuples(index=False):
            x = outcome_r(m, r.ts, r.side, r.entry, r.stop, r.target)
            if x is not None and np.isfinite(x):
                out.append(dict(r._asdict(), r=x))
    d = pd.DataFrame(out).dropna(subset=["r"]).sort_values("ts")     # proposals without a usable stop/target
    d["slot"] = d.ts // 1800
    d["first"] = ~d.duplicated(["sym", "side", "day", "slot"])
    return d


def to_markdown(d: pd.DataFrame) -> str:
    lines = ["# Does the AI judge help?", "",
             f"{len(d):,} escalations over {d.day.nunique()} sessions ({int(d['first'].sum()):,} after dedup). "
             "Each proposal traded on 1-minute candles (stop / target / 15:10), R after friction.", ""]
    for label, x in (("All escalations", d), ("Deduped (first per stock, side, 30 min)", d[d["first"]])):
        g = x.groupby("verdict").r.agg(["count", "mean", lambda s: (s > 0).mean()])
        g.columns = ["n", "mean R", "win"]
        lines += [f"## {label}", "", "| Verdict | n | Mean R | Win |", "|---|---|---|---|"]
        lines += [f"| {v} | {int(r.n)} | {r['mean R']:+.3f} | {r.win:.0%} |" for v, r in g.iterrows()]
        taken, held = x[x.verdict.isin(TAKEN)].r, x[~x.verdict.isin(TAKEN)].r
        lines += ["", f"Taken (CONFIRM/ADJUST): {taken.mean():+.3f} R over {len(taken)}; "
                      f"held back (DEFER/ABORT): {held.mean():+.3f} R over {len(held)}. "
                      f"The judge's value per decision: {taken.mean() - held.mean():+.3f} R.", ""]
        per_day = x.assign(taken=x.verdict.isin(TAKEN)).groupby(["day", "taken"]).r.mean().unstack()
        lines += ["By day (mean R: held back / taken):", "", "| Day | Held back | Taken |", "|---|---|---|"]
        lines += [f"| {dy} | {r.get(False, float('nan')):+.3f} | {r.get(True, float('nan')):+.3f} |" for dy, r in per_day.iterrows()]
        lines += [""]
    return "\n".join(lines)


def main():
    d = load()
    if d.empty:
        print("no escalations recorded")
        return
    md = to_markdown(d)
    from shadow.reanalysis import shadow_dir
    out = shadow_dir() / "reports"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"judge_{datetime.now(IST):%Y-%m-%d}.md"
    path.write_text(md, encoding="utf-8")
    print(md)
    print(f"\nSaved to {path}")


if __name__ == "__main__":
    main()
