"""The day/night clientele score (policy v8; EDGE_RESEARCH.md).

A stock's overnight (close -> open) and intraday (open -> close) returns are earned by different
investors and each part persists (Lou, Polk & Skouras 2019). Six factors from daily prices,
known at yesterday's close: the 5-, 20- and 60-day means of each part. Ranked across stocks each
day, combined by a ridge fitted on the last FIT_DAYS of the next day's open -> close return
(market-neutral, ranked). Research: 5 years +13 bps/position (t 5.3) for the top/bottom 2.5%,
13 months at tradable prices +8.7 bps after costs (auction entry, 15:10 exit).

Same definitions as research/preopen_v2.py + daily_research.py (walk-forward there; here the
fit is simply redone every night on the latest window).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

APP = Path(__file__).resolve().parents[1]
DATA = APP / "data"
FACTORS = ["intra5", "intra20", "intra60", "night5", "night20", "night60"]
FIT_DAYS, LAM = 250, 0.5
SKIP = {"NIFTY50", "BANKNIFTY", "NIFTYBANK"}


def load_daily(syms=None, data_dir: Path = DATA) -> dict:
    """{Open, Close, Volume}: date x symbol frames from data/<SYM>_1D.parquet."""
    fr = {}
    paths = ([data_dir / f"{s}_1D.parquet" for s in syms] if syms is not None
             else sorted(data_dir.glob("*_1D.parquet")))
    for p in paths:
        s = p.stem.replace("_1D", "")
        if s in SKIP or not p.exists():
            continue
        x = pd.read_parquet(p, columns=["Date", "Open", "Close", "Volume"])
        x["Date"] = pd.to_datetime(x.Date).dt.normalize()
        fr[s] = x.drop_duplicates("Date", keep="last").set_index("Date").sort_index()
    if not fr:
        return {}
    idx = sorted(set().union(*[f.index for f in fr.values()]))
    return {c: pd.DataFrame({s: f[c] for s, f in fr.items()}).reindex(idx) for c in ("Open", "Close", "Volume")}


def factors(P: dict) -> dict:
    C, O = P["Close"], P["Open"]
    intra = np.log(C / O).where(lambda x: x.abs() < 0.3)
    night = np.log(O / C.shift(1)).where(lambda x: x.abs() < 0.3)
    return {"intra5": intra.rolling(5).mean(), "intra20": intra.rolling(20, min_periods=15).mean(),
            "intra60": intra.rolling(60, min_periods=45).mean(), "night5": night.rolling(5).mean(),
            "night20": night.rolling(20, min_periods=15).mean(), "night60": night.rolling(60, min_periods=45).mean()}


def ranks(df: pd.DataFrame) -> pd.DataFrame:
    return df.rank(axis=1, pct=True) - 0.5


def label(P: dict) -> pd.DataFrame:
    """The next day's open -> close, market-neutral, aligned to the decision day."""
    C, O = P["Close"], P["Open"]
    y = np.log(C.shift(-1) / O.shift(-1)).where(lambda x: x.abs() < 0.3)
    return y.sub(y.mean(axis=1), axis=0)


def fit(P: dict, days: int = FIT_DAYS, lam: float = LAM) -> tuple[pd.Series, str]:
    """Ridge weights on the last `days` decision days whose label is known. Returns (w, last day)."""
    Fr = {k: ranks(v) for k, v in factors(P).items()}
    Y = ranks(label(P))
    usable = Y.index[Y.notna().sum(axis=1) >= max(10, 0.5 * Y.shape[1])]
    if len(usable) < 20:
        raise ValueError(f"auction fit: only {len(usable)} days with labels")
    tr = usable[-days:]
    A = np.column_stack([Fr[n].loc[tr].values.ravel() for n in FACTORS])
    b = Y.loc[tr].values.ravel()
    ok = np.isfinite(A).all(axis=1) & np.isfinite(b)
    A, b = A[ok], b[ok]
    w = np.linalg.solve(A.T @ A + lam * len(b) * np.eye(len(FACTORS)), A.T @ b)
    return pd.Series(w, index=FACTORS), str(tr[-1].date())


def score(P: dict, w: pd.Series) -> tuple[pd.Series, str]:
    """Scores from the last row of the daily data (yesterday's close for a morning plan)."""
    Fr = {k: ranks(v) for k, v in factors(P).items()}
    last = P["Close"].index[-1]
    X = np.column_stack([Fr[n].loc[last].fillna(0.0).values for n in FACTORS])
    have = np.isfinite(np.column_stack([factors(P)[n].loc[last].values for n in ("intra20", "night20")])).all(axis=1)
    s = pd.Series(X @ w.reindex(FACTORS).values, index=P["Close"].columns)
    return s[have], str(last.date())


def adv_crore(P: dict, days: int = 20) -> pd.Series:
    return (P["Close"] * P["Volume"]).tail(days).mean() / 1e7


def select(scores: pd.Series, n_per_side: int, adv: pd.Series | None = None, min_adv: float = 0.0) -> dict:
    """Top n longs and bottom n shorts among the liquid stocks."""
    s = scores.dropna()
    if adv is not None:
        s = s[adv.reindex(s.index).fillna(0.0) >= min_adv]
    s = s.sort_values()
    n = min(n_per_side, len(s) // 2)
    return {"long": list(s.index[::-1][:n]), "short": list(s.index[:n])}


def freshness(P: dict, today, synced: bool) -> tuple[bool, str]:
    """Daily data must end on the last session before `today`, for most stocks. There is no
    holiday calendar: after a successful sync from Upstox (`synced`) the newest stored day is
    the last session; without one, only the previous weekday is trusted."""
    C = P["Close"]
    last = C.index[-1]
    today = pd.Timestamp(today).normalize()
    if last >= today:
        return False, f"daily data already has {last.date()} (today): the plan must be made before the open"
    have = C.loc[last].notna().mean()
    if have < 0.9:
        return False, f"only {have:.0%} of stocks have a {last.date()} row"
    gap = int(np.busday_count(last.date(), today.date()))
    if not synced and gap > 1:
        return False, f"daily data ends {last.date()} ({gap} weekdays before {today.date()}) and the sync failed"
    if gap > 5:
        return False, f"daily data ends {last.date()}, {gap} weekdays before {today.date()}"
    return True, f"through {last.date()}"
