"""The alpha score's factor engine, shared by the live service, the nightly fit and the research
lab (dev-notes/trading-logic/research/alpha_lab.py imports it, so research and live cannot drift).

Panel: per stock, 5-minute bars (o/h/l/c/v) of shape (stocks, days, 75 bars from 09:15), plus
daily/option factors known before each day's open (daily_block). features() turns a panel into
one row per (day, decision bar k, stock): 24 factors (ALPHA_RESEARCH.md) and, outside live mode,
forward returns. barrier_labels() gives the v5.1 trade outcome in R (stop 1.5 x ATR15, target
1.5R, else 15:10) that the model is fitted on.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]                 # trading_copilot/
NB = 75                                   # 5-minute bars 09:15 .. 15:25
SLOTS = list(range(6, 52, 3))             # research decision bars: 09:45, 10:00, ..., 13:30
HORIZONS = {"y30": 6, "y60": 12, "y90": 18}
CLOSE_BAR = 70                            # the bar starting 15:05 closes at 15:10 (square-off)
STOP_ATR, TARGET_R = 1.5, 1.5             # v5.1: stop 1.5 x ATR15, target 1.5R
FACTORS = ["mom_open", "gap", "ret30", "ret60", "seas20", "rvol", "vwap_dev", "range_pos", "range_vol",
           "flow_open", "flow30", "prev_lasthr", "ret1d", "ret5d", "mom20", "cloc", "hi52", "ivrv", "pcrz",
           "oichg", "ivchg", "buildup", "maxpain", "lowvol"]


def _bars(path: Path):
    m = pd.read_parquet(path, columns=["ts", "o", "h", "l", "c", "v"])
    m["ts"] = pd.to_datetime(m.ts)
    mins = m.ts.dt.hour * 60 + m.ts.dt.minute - (9 * 60 + 15)
    m = m[(mins >= 0) & (mins < NB * 5)]
    m["day"] = m.ts.dt.normalize()
    m["j"] = (mins[m.index] // 5).astype(int)
    g = m.groupby(["day", "j"])
    b = pd.DataFrame({"o": g.o.first(), "h": g.h.max(), "l": g.l.min(), "c": g.c.last(), "v": g.v.sum()})
    return b


def daily_block(syms, days) -> dict:
    """Daily and option factors for each (stock, day): values known before that day's open
    (the previous session's close). A day after the last daily row (today, live) gets the
    last session's values."""
    S, D = len(syms), len(days)
    dcols = ["Open", "High", "Low", "Close", "Volume", "EOD_IV", "EOD_PCR", "EOD_MAX_PAIN", "Total_OI"]
    daily = {k: np.full((S, D), np.nan, dtype=np.float64) for k in
             ["sig", "ret1d", "ret5d", "mom20", "cloc", "hi52", "ivrv", "pcrz", "oichg", "mp", "pc_daily", "ivchg"]}
    for si, s in enumerate(syms):
        p = ROOT / "data" / f"{s}_1D.parquet"
        if not p.exists():
            continue
        x = pd.read_parquet(p)
        x["Date"] = pd.to_datetime(x.Date).dt.normalize()
        x = x.reindex(columns=["Date"] + dcols)
        x = x.set_index("Date").sort_index()
        x = x[~x.index.duplicated()]
        extra = [d for d in pd.DatetimeIndex(days) if d > x.index.max()]
        if extra:                                          # today: a row to shift into
            x = pd.concat([x, pd.DataFrame(index=pd.DatetimeIndex(extra), columns=x.columns, dtype=float)])
        lc = np.log(x.Close)
        r = lc.diff()
        r[(r.abs() > 0.3)] = np.nan                       # splits / bad prints
        f = pd.DataFrame(index=x.index)
        f["sig"] = r.rolling(20, min_periods=15).std().shift(1)
        f["ret1d"] = r.shift(1)
        f["ret5d"] = r.rolling(5).sum().shift(1)
        f["mom20"] = r.rolling(20).sum().shift(2)
        f["cloc"] = ((x.Close - x.Low) / (x.High - x.Low)).shift(1) - 0.5
        f["hi52"] = np.log(x.Close / x.High.rolling(250, min_periods=120).max()).shift(1)
        f["ivrv"] = (x.EOD_IV / (r.rolling(20, min_periods=15).std() * np.sqrt(252) * 100)).shift(1)
        pc = x.EOD_PCR.shift(1)
        f["pcrz"] = (pc - pc.rolling(60, min_periods=30).mean()) / pc.rolling(60, min_periods=30).std()
        oi = np.log(x.Total_OI.replace(0, np.nan))
        f["oichg"] = oi.diff().shift(1)
        f["mp"] = x.EOD_MAX_PAIN.shift(1)
        f["pc_daily"] = x.Close.shift(1)
        f["ivchg"] = np.log(x.EOD_IV.replace(0, np.nan)).diff().shift(1)
        f = f.reindex(pd.DatetimeIndex(days))
        for k in daily:
            daily[k][si] = f[k].values
    return daily


def features(P, live: bool = False, slots=None) -> pd.DataFrame:
    """One row per (day, decision slot, stock): factors and targets."""
    O, H, L, C, V = P["o"], P["h"], P["l"], P["c"], P["v"]
    S, D, _ = C.shape
    sig = P["d_sig"]
    sig = np.where(sig > 0.003, sig, np.nan)
    lc = np.log(C.astype(np.float64))
    prev_last = np.concatenate([np.full((S, 1), np.nan), C[:, :-1, NB - 1]], axis=1)    # yesterday's last bar
    prev_lasthr = np.concatenate([np.full((S, 1), np.nan),
                                  (lc[:, :-1, NB - 1] - lc[:, :-1, NB - 13])], axis=1)
    r5 = np.diff(lc, axis=2, prepend=np.nan)                                            # 5-min log returns
    r5[:, :, 0] = lc[:, :, 0] - np.log(O[:, :, 0])
    # per-bar volume-signed flow (bulk volume classification, Easley-Lopez de Prado-O'Hara 2012)
    s5 = np.nanstd(r5[:, :, 1:].reshape(S, -1), axis=1)[:, None, None]
    z = np.clip(np.nan_to_num(r5 / s5), -6, 6)
    from scipy.stats import norm
    bvc = V * (2 * norm.cdf(z) - 1)
    cumV = np.nancumsum(V, axis=2)
    tp = (H + L + C) / 3.0
    cumPV = np.nancumsum(tp * V, axis=2)
    rows = []
    for k in (slots or SLOTS):
        P0 = C[:, :, k - 1]
        f = {}
        f["mom_open"] = (lc[:, :, k - 1] - np.log(O[:, :, 0])) / sig
        f["gap"] = (np.log(O[:, :, 0]) - np.log(prev_last)) / sig
        f["ret30"] = (lc[:, :, k - 1] - lc[:, :, max(k - 7, 0)]) / sig
        f["ret60"] = (lc[:, :, k - 1] - lc[:, :, max(k - 13, 0)]) / sig
        # same-window seasonality, Heston-Korajczyk-Sadka (2010): this window's return on the last 20 days
        h = HORIZONS["y60"]
        win = lc[:, :, min(k - 1 + h, NB - 1)] - lc[:, :, k - 1]
        f["seas20"] = pd.DataFrame(win.T).shift(1).rolling(20, min_periods=10).mean().T.values / sig
        # relative volume: volume so far today vs the same time on the last 20 days
        vs = cumV[:, :, k - 1]
        base = pd.DataFrame(vs.T).shift(1).rolling(20, min_periods=10).mean().T.values
        f["rvol"] = np.log((vs + 1) / (base + 1))
        vwap = cumPV[:, :, k - 1] / np.where(cumV[:, :, k - 1] > 0, cumV[:, :, k - 1], np.nan)
        f["vwap_dev"] = np.log(P0 / vwap) / sig
        hi = np.nanmax(H[:, :, :k], axis=2)
        lo = np.nanmin(L[:, :, :k], axis=2)
        f["range_pos"] = (P0 - lo) / np.where(hi > lo, hi - lo, np.nan) - 0.5
        f["range_vol"] = np.log(hi / lo) / sig                                          # today's range so far
        f["flow_open"] = np.nansum(bvc[:, :, :k], axis=2) / np.where(vs > 0, vs, np.nan)
        v30 = np.nansum(V[:, :, max(k - 6, 0):k], axis=2)
        f["flow30"] = np.nansum(bvc[:, :, max(k - 6, 0):k], axis=2) / np.where(v30 > 0, v30, np.nan)
        f["prev_lasthr"] = prev_lasthr / sig
        for n in ("ret1d", "ret5d", "mom20", "cloc", "hi52", "ivrv", "pcrz", "oichg", "ivchg"):
            f[n] = P[f"d_{n}"] / (sig if n in ("ret1d", "ret5d", "mom20") else 1.0)
        f["buildup"] = P["d_oichg"] * np.sign(P["d_ret1d"])                             # long/short build-up
        f["maxpain"] = np.log(P["d_mp"] / P0) / sig
        f["lowvol"] = -np.log(sig)
        # targets (market-neutral, vol-scaled) and raw returns (bps) for the spread
        t = {}
        for name, hh in HORIZONS.items():
            t[name] = lc[:, :, min(k - 1 + hh, NB - 1)] - lc[:, :, k - 1]
        t["yclose"] = lc[:, :, CLOSE_BAR] - lc[:, :, k - 1]
        dd, ss = np.meshgrid(np.arange(D), np.arange(S))
        frame = {"d": dd.ravel(), "s": ss.ravel(), "k": np.full(S * D, k)}
        for n, v in f.items():
            frame[n] = np.asarray(v, dtype=np.float64).ravel()
        for n, v in t.items():
            frame["raw_" + n] = np.asarray(v, dtype=np.float64).ravel()
        frame["sig"] = sig.ravel()
        frame["px"] = np.asarray(P0, dtype=np.float64).ravel()
        rows.append(pd.DataFrame(frame))
    X = pd.concat(rows, ignore_index=True)
    X = X[np.isfinite(X.px) & np.isfinite(X.sig) & (live | np.isfinite(X.raw_y60))]
    g = X.groupby(["d", "k"])
    for n in list(HORIZONS) + ["yclose"]:
        X["mn_" + n] = X["raw_" + n] - g["raw_" + n].transform("mean")          # market-neutral
        X[n] = X["mn_" + n] / X.sig                                              # ... and vol-scaled
    X = X[g.s.transform("size") >= 50]                                           # a real cross-section
    return X.reset_index(drop=True)


def ranked(X: pd.DataFrame, cols) -> pd.DataFrame:
    """Cross-sectional rank per decision time, centred to [-0.5, 0.5]; missing -> 0 (neutral)."""
    R = X.groupby(["d", "k"])[cols].rank(pct=True) - 0.5
    return R.fillna(0.0)


def ridge(A: np.ndarray, y: np.ndarray, lam: float) -> np.ndarray:
    n = A.shape[1]
    return np.linalg.solve(A.T @ A + lam * len(y) * np.eye(n), A.T @ y)


def atr15(P) -> np.ndarray:
    """ATR15 (mean true range of the last 14 completed 15-minute bars) at each 15-minute mark,
    shape (S, D, 25): index g = ATR after 15-minute bar g has closed."""
    H, L, C = P["h"], P["l"], P["c"]
    S, D, _ = C.shape
    h15 = np.nanmax(H.reshape(S, D, 25, 3), axis=3).reshape(S, -1)
    l15 = np.nanmin(L.reshape(S, D, 25, 3), axis=3).reshape(S, -1)
    c15 = C.reshape(S, D, 25, 3)[:, :, :, 2].reshape(S, -1)
    pc = np.concatenate([np.full((S, 1), np.nan), c15[:, :-1]], axis=1)
    tr = np.nanmax(np.stack([h15 - l15, np.abs(h15 - pc), np.abs(l15 - pc)]), axis=0)
    atr = pd.DataFrame(tr.T).rolling(14, min_periods=10).mean().T.values
    return atr.reshape(S, D, 25)


def barrier_labels(P) -> pd.DataFrame:
    """R after friction of a v5.1-style trade opened at each decision time, long and short:
    stop STOP_ATR x ATR15, target TARGET_R x risk, else closed at 15:10. Stop first when both
    are touched inside one 5-minute bar (conservative)."""
    H, L, C = P["h"].astype(np.float64), P["l"].astype(np.float64), P["c"].astype(np.float64)
    S, D, _ = C.shape
    A = atr15(P)
    out = []
    for k in SLOTS:
        P0 = C[:, :, k - 1]
        a = A[:, :, k // 3 - 1]
        d = STOP_ATR * a
        fh, fl = H[:, :, k:CLOSE_BAR + 1], L[:, :, k:CLOSE_BAR + 1]
        last = C[:, :, CLOSE_BAR]
        fr = 0.00072 * P0 / d                                              # friction in R
        res = {}
        for side, sg in (("RL", 1), ("RS", -1)):
            stop = P0 - sg * d
            tgt = P0 + sg * TARGET_R * d
            hit_s = (fl <= stop[:, :, None]) if sg > 0 else (fh >= stop[:, :, None])
            hit_t = (fh >= tgt[:, :, None]) if sg > 0 else (fl <= tgt[:, :, None])
            n = fh.shape[2]
            i_s = np.where(hit_s.any(axis=2), hit_s.argmax(axis=2), n)
            i_t = np.where(hit_t.any(axis=2), hit_t.argmax(axis=2), n)
            r = np.where(i_s <= i_t, np.where(i_s < n, -1.0, np.nan), TARGET_R)
            open_ = (i_s == n) & (i_t == n)
            r = np.where(open_, sg * (last - P0) / d, r)
            res[side] = r - fr
        dd, ss = np.meshgrid(np.arange(D), np.arange(S))
        out.append(pd.DataFrame({"d": dd.ravel(), "s": ss.ravel(), "k": k, "RL": res["RL"].ravel(),
                                 "RS": res["RS"].ravel(), "atr15": a.ravel()}))
    return pd.concat(out, ignore_index=True)


def fill_bars(arr: dict) -> dict:
    """Within a day, a 5-minute bar with no trade carries the last close (o=h=l=c, v=0)."""
    c = arr["c"]
    for j in range(1, NB):
        miss = np.isnan(c[..., j]) & ~np.isnan(c[..., j - 1])
        c[..., j][miss] = c[..., j - 1][miss]
        for k in "ohl":
            arr[k][..., j][miss] = c[..., j][miss]
        arr["v"][..., j][miss] = 0.0
    return arr


def panel_from_store(syms=None, last_days: int | None = None) -> dict:
    """The panel from the local 1-minute store (data/intraday): every stock, or `syms`; every
    session, or the last `last_days`."""
    files = sorted((ROOT / "data" / "intraday").glob("*.parquet"))
    if syms is not None:
        want = {str(s) for s in syms}
        files = [f for f in files if f.stem in want]
    syms = [f.stem for f in files]
    per = {s: _bars(f) for s, f in zip(syms, files)}
    days = sorted(set().union(*[set(b.index.get_level_values(0)) for b in per.values()]))
    if last_days:
        days = days[-last_days:]
    di = {d: i for i, d in enumerate(days)}
    S, D = len(syms), len(days)
    arr = {k: np.full((S, D, NB), np.nan, dtype=np.float32) for k in "ohlcv"}
    for si, s in enumerate(syms):
        b = per[s]
        keep = b.index.get_level_values(0).isin(days)
        b = b[keep]
        dd = np.array([di[x] for x in b.index.get_level_values(0)])
        jj = b.index.get_level_values(1).values
        for k in "ohlcv":
            arr[k][si, dd, jj] = b[k].values
    fill_bars(arr)
    P = {"syms": np.array(syms), "days": np.array([str(d.date()) for d in days]), **arr}
    for k, v in daily_block(syms, days).items():
        P[f"d_{k}"] = v
    return P


def labelled(P) -> pd.DataFrame:
    """features() + the v5.1 barrier outcome: RD_mn = directional R, market-neutral."""
    X = features(P)
    lab = barrier_labels(P)
    X = X.merge(lab, on=["d", "s", "k"], how="left")
    X = X[np.isfinite(X.RL) & np.isfinite(X.RS) & (X.atr15 > 0)]
    X["RD"] = (X.RL - X.RS) / 2
    X["RD_mn"] = X.RD - X.groupby(["d", "k"]).RD.transform("mean")
    return X


def fit(X: pd.DataFrame, sessions: int = 60, lam: float = 0.5) -> pd.Series:
    """Ridge on cross-sectional ranks over the last `sessions` days: the live weights."""
    days = np.sort(X.d.unique())[-sessions:]
    X = X[X.d.isin(days)]
    R = ranked(X, FACTORS)
    y = X.groupby(["d", "k"]).RD_mn.rank(pct=True) - 0.5
    ok = y.notna()
    return pd.Series(ridge(R.loc[ok, FACTORS].values, y[ok].values, lam), index=FACTORS)


def score(X: pd.DataFrame, w: pd.Series) -> pd.Series:
    """Score per row: ranks within each (day, bar) dotted with the weights."""
    R = ranked(X, list(w.index))
    return pd.Series(R[list(w.index)].values @ w.values, index=X.index)

