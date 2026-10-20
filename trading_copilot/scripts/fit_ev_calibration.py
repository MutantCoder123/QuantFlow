"""Fit data/ev_calibration.json for policy v6's ev_gate.

Every logged reading (feature log, one per stock per 5 minutes, 09:45-13:40,
|score| >= 0.15) is traded the v6 way on the exchange's 1-minute candles:
side = the score's sign, entry = that minute's close, stop = stop_floor x
ATR15 x time-of-day factor, target = target_min_r R, out at the stop, the
target or 15:10. Result in R minus friction (0.072% of the position value,
measured on 69 paper trades: friction R = 0.00072 x entry / stop distance).
Averaged per regime and |score| band.

Readings of one stock 5 minutes apart overlap, so `n` overstates the
independent evidence; `stock_days` and `days_positive` are kept beside it.

usage: python trading_copilot/scripts/fit_ev_calibration.py [--exclude 2026-10-07] [--out name.json]
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

APP = Path(__file__).resolve().parent.parent
for p in (APP, APP / "data_services"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

IST = ZoneInfo("Asia/Kolkata")
BANDS = [(0.15, 0.25), (0.25, 0.35), (0.35, 0.5), (0.5, 1.01)]
FRICTION = 0.00072
START, CUTOFF, SQUARE_OFF = "09:45", "13:40", "15:10"


def readings(day: str) -> pd.DataFrame:
    parts = []
    for p in glob.glob(str(APP / "data" / "features" / f"date={day}" / "*.parquet")):
        have = set(pq.read_schema(p).names)
        cols = [c for c in ("ts", "symbol", "composite", "regime", "f_atr_15m", "decision") if c in have]
        if "f_atr_15m" not in cols:
            continue
        parts.append(pd.read_parquet(p, columns=cols))
    if not parts:
        return pd.DataFrame()
    f = pd.concat(parts).dropna(subset=["composite", "f_atr_15m"])
    f = f[(f.composite.abs() >= 0.15) & (f.f_atr_15m > 0)]
    f["t"] = pd.to_datetime(f.ts, unit="s", utc=True).dt.tz_convert(IST)
    hm = f.t.dt.strftime("%H:%M")
    f = f[(hm >= START) & (hm <= CUTOFF)]
    if "decision" in f:
        f = f[~f.decision.astype(str).str.contains("DATA_INCOMPLETE|STALE")]
    f["slot"] = f.t.dt.floor("5min")
    return f.sort_values("ts").groupby(["symbol", "slot"]).last().reset_index()


def minutes(sym: str, day: str) -> pd.DataFrame | None:
    f = APP / "data" / "intraday" / f"{sym}.parquet"
    m = None
    if f.exists():
        m = pd.read_parquet(f)
        m["ts"] = pd.to_datetime(m.ts)
        m = m[m.ts.dt.strftime("%Y-%m-%d") == day]
    if m is None or m.empty:
        try:
            from shadow.report import _candles
            c = _candles(sym, day, APP / "data" / "shadow" / "cache")
            m = pd.DataFrame(c, columns=["ts", "o", "h", "l", "c"])
            m["ts"] = pd.to_datetime(m.ts, unit="s", utc=True).dt.tz_convert(IST).dt.tz_localize(None)
        except Exception:
            return None
    return m.sort_values("ts").reset_index(drop=True) if len(m) else None


def trade_r(m: pd.DataFrame, t0, side: int, stop_d: float, tgt_d: float) -> float | None:
    after = m[(m.ts > t0) & (m.ts.dt.strftime("%H:%M") < SQUARE_OFF)]
    at = m[m.ts <= t0]
    if after.empty or at.empty:
        return None
    e = float(at.c.iloc[-1])
    lo, hi = after.l.values, after.h.values
    adv = (e - lo) if side > 0 else (hi - e)
    fav = (hi - e) if side > 0 else (e - lo)
    s_hit = np.argmax(adv >= stop_d) if (adv >= stop_d).any() else None
    t_hit = np.argmax(fav >= tgt_d) if (fav >= tgt_d).any() else None
    if s_hit is not None and (t_hit is None or s_hit <= t_hit):
        r = -1.0
    elif t_hit is not None:
        r = tgt_d / stop_d
    else:
        r = side * (float(after.c.iloc[-1]) - e) / stop_d
    return r - FRICTION * e / stop_d


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exclude", action="append", default=[])
    ap.add_argument("--out", default=None)
    ap.add_argument("--days", default=None, help="comma list; default every feature-log day")
    a = ap.parse_args(argv)
    from core.policy_config import load_policy
    from core.tod_vol import factor, table
    from paths import CONFIG_DIR, DATA_DIR
    pol = load_policy(CONFIG_DIR / "policy_v6.yaml").conviction
    floor_k, tmr = float(pol.get("stop_floor_atr15", 2.0)), float(pol.get("target_min_r", 1.5))
    tod = table()
    days = a.days.split(",") if a.days else sorted(Path(p).name.split("=")[1]
                                                   for p in glob.glob(str(APP / "data" / "features" / "date=*")))
    days = [d for d in days if d not in a.exclude]
    rows = []
    for day in days:
        f = readings(day)
        if f.empty:
            continue
        for sym, g in f.groupby("symbol"):
            m = minutes(sym, day)
            if m is None:
                continue
            for r in g.itertuples(index=False):
                t0 = r.t.tz_localize(None) if r.t.tzinfo else r.t
                stop_d = floor_k * r.f_atr_15m * factor(sym, r.t.to_pydatetime(), tod.get(sym))
                x = trade_r(m, t0, 1 if r.composite > 0 else -1, stop_d, tmr * stop_d)
                if x is not None:
                    rows.append({"day": day, "sym": sym, "regime": r.regime, "s": abs(r.composite), "r": x})
        print(f"  {day}: {sum(1 for x in rows if x['day'] == day):,} readings traded", flush=True)
    d = pd.DataFrame(rows)
    out = {"fitted_at": datetime.now(IST).strftime("%Y-%m-%d %H:%M"), "days": sorted(d.day.unique().tolist()),
           "excluded": a.exclude, "geometry": {"stop_floor_atr15": floor_k, "target_min_r": tmr,
                                                "friction_pct": FRICTION * 100, "exit": "stop/target/15:10"},
           "regimes": {}, "detail": {}}
    for rg, g in d.groupby("regime"):
        bands, detail = [], []
        for lo, hi in BANDS:
            b = g[(g.s >= lo) & (g.s < hi)]
            if b.empty:
                continue
            per_day = b.groupby("day").r.mean()
            bands.append([lo, hi, round(float(b.r.mean()), 4), int(len(b))])
            detail.append({"band": [lo, hi], "mean_r": round(float(b.r.mean()), 4), "n": int(len(b)),
                           "stock_days": int(b.groupby(["day", "sym"]).ngroups),
                           "days_positive": f"{int((per_day > 0).sum())}/{len(per_day)}",
                           "win_rate": round(float((b.r > 0).mean()), 3)})
        out["regimes"][rg] = bands
        out["detail"][rg] = detail
    path = DATA_DIR / (a.out or load_policy(CONFIG_DIR / "policy_v6.yaml").conviction["ev_gate"]["file"])
    from core.fileio import write_json_atomic
    write_json_atomic(str(path), out, indent=1)
    for rg, det in out["detail"].items():
        for x in det:
            print(f"  {rg:24s} {x['band']}  mean {x['mean_r']:+.3f} R  n {x['n']:5d}  stock-days {x['stock_days']:4d}  "
                  f"days+ {x['days_positive']}  win {x['win_rate']:.0%}")
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
