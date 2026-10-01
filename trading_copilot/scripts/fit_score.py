"""How well does the conviction score -- and each input it could use --
predict the move that follows? Fits candidate weights on the feature log and
tests them on days the fit did not see.

    python trading_copilot/scripts/fit_score.py                 # every logged day
    python trading_copilot/scripts/fit_score.py --horizon 90    # minutes ahead (default 90)

It RECOMMENDS ONLY. It writes data/paper/reports/score_fit_<last day>.md and
changes no weight: a fit that holds up on unseen days goes into a new policy
version by hand, like every other change (see config/policy_v*.yaml).

Method
  * One evaluation per symbol per minute, 09:45-15:10 (policy v3's trading
    window), from data/features/date=*/.
  * Forward return from the exchange's 1-minute closes (Upstox candles), not
    from the feed, so restarts and outages do not bias it.
  * Skill = rank correlation (IC) between a value and the forward return,
    per day; an input counts as stable only if its IC has the same sign on
    every day.
  * Candidate weights: ridge regression on within-day ranks, validated
    leave-one-day-out -- each day is predicted by a fit on the other days.
    With fewer than 10 days the report says the fit is not yet usable.
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / "data_services"))

MIN_DAYS_TO_USE = 10
WINDOW = ("09:45", "15:10")

# Raw inputs worth testing; derived ones are computed in build_inputs().
RAW = ["f_obi", "f_cvd", "f_whale_cvd_slope", "f_whale_cvd_ema_1h", "f_price_to_vwap_pct", "f_rsi_5m",
       "f_rsi_15m", "f_rsi_1h", "f_macd_hist_5m", "f_macd_hist_15m", "f_vol_z_score_5m",
       "f_alpha_vs_nifty_5d", "f_cmf_5m", "f_distance_to_poc_pct", "f_ivr"]
DERIVED = ["ema_gap_5m", "ema_gap_1h", "stretch_1h", "day_move"]


def build_inputs(f: pd.DataFrame) -> pd.DataFrame:
    f = f.copy()
    f["ema_gap_5m"] = (f.f_ema_9_5m - f.f_ema_21_5m) / f.f_ltp * 100
    f["ema_gap_1h"] = (f.f_ema_9_1h - f.f_ema_21_1h) / f.f_ltp * 100
    f["stretch_1h"] = (f.f_ltp - f.f_ema_21_1h) / f.f_atr_1h
    f["day_move"] = (f.f_ltp / f.f_prev_close - 1) * 100
    return f


def ic(x: pd.Series, y: pd.Series) -> float:
    ok = x.notna() & y.notna() & np.isfinite(x) & np.isfinite(y)
    return float(x[ok].rank().corr(y[ok].rank())) if ok.sum() > 50 else float("nan")


def load_day(day: str) -> pd.DataFrame:
    paths = glob.glob(str(APP / "data" / "features" / f"date={day}" / "*.parquet"))
    f = pd.concat([pd.read_parquet(p) for p in paths])
    f = f[(f.regime != "MARKET_CLOSED") & f.composite.notna()]
    f["t"] = pd.to_datetime(f.ts, unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    lo, hi = (pd.Timestamp(x).time() for x in WINDOW)
    f = f[(f.t.dt.time >= lo) & (f.t.dt.time < hi)]
    f["minute"] = f.t.dt.floor("1min")
    return f.sort_values("t").groupby(["symbol", "minute"]).tail(1)


def exchange_closes(sym: str, day: str, headers: dict) -> pd.Series:
    import requests
    import scrip_master_engine
    k = scrip_master_engine.get_instrument_key(sym)
    today = datetime.now().strftime("%Y-%m-%d")
    url = (f"https://api.upstox.com/v3/historical-candle/intraday/{k}/minutes/1" if day == today
           else f"https://api.upstox.com/v3/historical-candle/{k}/minutes/1/{day}/{day}")
    c = requests.get(url, headers=headers, timeout=20).json()["data"]["candles"]
    return pd.Series({pd.Timestamp(x[0]).tz_convert("Asia/Kolkata").tz_localize(None): x[4] for x in c}).sort_index()


def with_returns(f: pd.DataFrame, day: str, horizon: int, headers: dict) -> pd.DataFrame:
    out = []
    for sym, g in f.groupby("symbol"):
        try:
            c = exchange_closes(sym, day, headers)
        except Exception as e:
            print(f"  no exchange candles for {sym} {day}: {e}")
            continue
        g = g.copy()
        g["fwd"] = (c.reindex(g.minute + pd.Timedelta(minutes=horizon)).values / c.reindex(g.minute).values - 1) * 100
        g["day"] = day
        out.append(g)
    return pd.concat(out) if out else pd.DataFrame()


def ridge(X: np.ndarray, y: np.ndarray, lam: float = 10.0) -> np.ndarray:
    return np.linalg.solve(X.T @ X + lam * np.eye(X.shape[1]), X.T @ y)


def ranked(rows: pd.DataFrame, cols: list) -> pd.DataFrame:
    """Each input and the forward return as its within-day percentile, -0.5
    to +0.5: the fit then works on what IC measures (order, not size), and a
    share-count input like CVD cannot swamp the rest."""
    d = rows.dropna(subset=cols + ["fwd"]).copy()
    for c in cols + ["fwd"]:
        d[c] = d.groupby("day")[c].rank(pct=True) - 0.5
    return d


def leave_one_day_out(rows: pd.DataFrame, cols: list) -> tuple[dict, pd.Series]:
    """Out-of-sample IC per held-out day, and the weights fitted on all days."""
    d = ranked(rows, cols)
    oos = {}
    for day in sorted(d.day.unique()):
        tr, te = d[d.day != day], d[d.day == day]
        if tr.empty or te.empty:
            continue
        mu, sd = tr[cols].mean(), tr[cols].std().replace(0, 1)
        w = ridge(((tr[cols] - mu) / sd).values, tr.fwd.values)
        pred = pd.Series(((te[cols] - mu) / sd).values @ w, index=te.index)
        oos[day] = ic(pred, te.fwd)
    mu, sd = d[cols].mean(), d[cols].std().replace(0, 1)
    w_all = pd.Series(ridge(((d[cols] - mu) / sd).values, d.fwd.values), index=cols)
    return oos, w_all


def report(rows: pd.DataFrame, horizon: int) -> str:
    days = sorted(rows.day.unique())
    L = [f"# Score fit report: {horizon}-minute horizon", "",
         f"{len(days)} day(s) ({days[0]} to {days[-1]}), {len(rows):,} symbol-minutes, {rows.symbol.nunique()} symbols, "
         f"window {WINDOW[0]}-{WINDOW[1]} IST.", "",
         "> This report recommends only. No weight changes from it; a fit that holds on unseen days "
         "goes into a new policy version by hand.", ""]

    L += ["## The live score", "", "| | " + " | ".join(days) + " | all |", "|---|" + "---|" * (len(days) + 1)]
    L.append("| IC | " + " | ".join(f"{ic(g.composite, g.fwd):+.3f}" for _, g in rows.groupby("day"))
             + f" | {ic(rows.composite, rows.fwd):+.3f} |")
    s = rows[(rows.composite.abs() >= 0.15) & rows.fwd.notna()]
    L += ["", f"When |score| >= 0.15 ({len(s):,} rows) the direction was right "
              f"{(np.sign(s.composite) == np.sign(s.fwd)).mean():.1%} of the time; mean signed move "
              f"{(np.sign(s.composite) * s.fwd).mean():+.3f}%.", ""]
    L += ["By regime:", "", "| regime | rows | IC |", "|---|---|---|"]
    for r, g in rows.groupby("regime"):
        L.append(f"| {r} | {len(g):,} | {ic(g.composite, g.fwd):+.3f} |")

    L += ["", "## Each input", "", "Stable = the same sign of IC on every day.", "",
          "| input | IC all | " + " | ".join(days) + " | stable |", "|---|---|" + "---|" * len(days) + "---|"]
    table = []
    for c in RAW + DERIVED:
        if c not in rows:
            continue
        per = [ic(g[c], g.fwd) for _, g in rows.groupby("day")]
        stable = all(np.sign(p) == np.sign(per[0]) for p in per if np.isfinite(p)) and len(days) > 1
        table.append((c, ic(rows[c], rows.fwd), per, stable))
    for c, a, per, st in sorted(table, key=lambda t: -abs(t[1]) if np.isfinite(t[1]) else 0):
        L.append(f"| {c} | {a:+.3f} | " + " | ".join(f"{p:+.3f}" for p in per) + f" | {'yes' if st else 'no'} |")

    cols = [c for c, a, per, st in table if st and np.isfinite(a)]
    L += ["", "## Candidate weights (stable inputs only, ridge on within-day ranks)", ""]
    if len(days) < 2 or not cols:
        L.append("_Needs at least two days and one stable input._")
    else:
        oos, w = leave_one_day_out(rows, cols)
        L += ["Out-of-sample IC, each day predicted by a fit on the others: "
              + ", ".join(f"{d} {v:+.3f}" for d, v in oos.items()) + ".", "",
              "| input | weight |", "|---|---|"]
        L += [f"| {c} | {v:+.4f} |" for c, v in w.sort_values(key=abs, ascending=False).items()]
        live = {d: ic(g.composite, g.fwd) for d, g in rows.groupby("day")}
        better = sum(1 for d in oos if np.isfinite(oos[d]) and oos[d] > live.get(d, -1))
        L += ["", f"The fit beat the live score out of sample on {better} of {len(oos)} day(s)."]
        if len(days) < MIN_DAYS_TO_USE:
            L.append(f"**Not yet usable:** {len(days)} day(s); a fit needs {MIN_DAYS_TO_USE} or more before it "
                     "is worth a policy version.")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--horizon", type=int, default=90)
    a = ap.parse_args()
    token = json.load(open(APP / "upstox_token.json"))["access_token"]
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    days = sorted(p.split("date=")[-1] for p in glob.glob(str(APP / "data" / "features" / "date=*")))
    frames = []
    for day in days:
        f = load_day(day)
        if f.empty:
            continue
        print(f"{day}: {len(f):,} symbol-minutes")
        frames.append(with_returns(build_inputs(f), day, a.horizon, headers))
    rows = pd.concat([x for x in frames if not x.empty])
    md = report(rows, a.horizon)
    out = APP / "data" / "paper" / "reports" / f"score_fit_{rows.day.max()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
