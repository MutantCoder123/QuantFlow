"""Pick the 50 streamed stocks with the alpha score before the open (run.py, policy v7).

Upstox's 30-level feed takes at most 50 stocks. The alpha service ranks all F&O stocks every 5
minutes, but only streamed stocks can be traded. So before the open the watchlist is filled
with the stocks the alpha score is most likely to select:
  - pinned (config/watchlist_policy.yaml alpha_select.pinned) and any open paper position;
  - then, alternately, the highest and lowest pre-open scores among liquid stocks
    (20-day traded value >= min_adv_crore).
Pre-open score = the alpha weights on the factors known before the open (daily and options:
ret1d, ret5d, mom20, cloc, hi52, ivrv, pcrz, oichg, ivchg, buildup, lowvol, maxpain). Intraday
factors are neutral. The previous watchlist is kept in data/watchlist_history/.

usage: python trading_copilot/alpha/select_watchlist.py [--dry-run]
"""
from __future__ import annotations

import csv
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
APP = HERE.parent
for p in (APP, APP / "data_services"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from alpha import core  # noqa: E402

PREOPEN = ["ret1d", "ret5d", "mom20", "cloc", "hi52", "ivrv", "pcrz", "oichg", "ivchg", "buildup", "lowvol", "maxpain"]
WATCHLIST = APP / "watchlist.csv"


def policy() -> dict:
    import yaml
    p = yaml.safe_load((APP / "config" / "watchlist_policy.yaml").read_text(encoding="utf-8")) or {}
    return {"enabled": False, "size": 50, "pinned": [], "min_adv_crore": 50.0, **(p.get("alpha_select") or {})}


def preopen_scores(syms, day: str, w: pd.Series) -> pd.DataFrame:
    """The pre-open factors for `day` (from the daily files) ranked across stocks, and the score."""
    hist = sorted({str(d.date()) for d in pd.bdate_range(end=day, periods=320)})
    days = [pd.Timestamp(d) for d in hist]
    D = core.daily_block(syms, days)
    i = len(days) - 1
    sig = np.where(D["sig"][:, i] > 0.003, D["sig"][:, i], np.nan)
    f = {n: D[n][:, i] / (sig if n in ("ret1d", "ret5d", "mom20") else 1.0)
         for n in ("ret1d", "ret5d", "mom20", "cloc", "hi52", "ivrv", "pcrz", "oichg", "ivchg")}
    f["buildup"] = D["oichg"][:, i] * np.sign(D["ret1d"][:, i])
    f["lowvol"] = -np.log(sig)
    f["maxpain"] = np.log(D["mp"][:, i] / D["pc_daily"][:, i]) / sig
    X = pd.DataFrame(f, index=list(syms))
    R = X.rank(pct=True) - 0.5
    R = R.fillna(0.0)
    X["score"] = R[PREOPEN].values @ w.reindex(PREOPEN).fillna(0.0).values
    return X


def adv_crore(sym: str) -> float:
    try:
        d = pd.read_parquet(APP / "data" / f"{sym}_1D.parquet", columns=["Close", "Volume"]).tail(20)
        return float((d.Close * d.Volume).mean() / 1e7)
    except Exception:
        return 0.0


def open_positions() -> list:
    """Stocks with an open paper position (from the paper event store)."""
    try:
        from paper.store import EventStore, rebuild
        st = rebuild(EventStore().load())
        return sorted({p["symbol"] for p in st.open.values()})
    except Exception:
        return []


def select(scores: pd.DataFrame, pinned: list, size: int, min_adv: float, adv=adv_crore) -> list:
    """Pinned first, then alternately the best long and best short candidate. Pure apart from `adv`."""
    out = [s for s in dict.fromkeys(pinned)]
    ranked = scores.score.dropna().sort_values()
    longs = [s for s in ranked.index[::-1] if s not in out]
    shorts = [s for s in ranked.index if s not in out]
    li = si = 0
    while len(out) < size and (li < len(longs) or si < len(shorts)):
        for pool, idx in ((longs, "l"), (shorts, "s")):
            if len(out) >= size:
                break
            j = li if idx == "l" else si
            while j < len(pool) and (pool[j] in out or adv(pool[j]) < min_adv):
                j += 1
            if j < len(pool):
                out.append(pool[j])
                j += 1
            if idx == "l":
                li = j
            else:
                si = j
    return out[:size]


def write_watchlist(symbols: list, path: Path = WATCHLIST) -> int:
    import scrip_master_engine
    hist = APP / "data" / "watchlist_history"
    hist.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy(path, hist / f"watchlist_{datetime.now():%Y-%m-%d_%H%M}.csv")
    rows = []
    for s in symbols:
        tok = scrip_master_engine.get_exchange_token(s)
        if tok:
            rows.append([tok, s, "NSE"])
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Token", "Symbol", "Exchange"])
        w.writerows(rows)
    return len(rows)


def main() -> int:
    pol = policy()
    if not pol["enabled"]:
        print("alpha watchlist selection is off (watchlist_policy.yaml alpha_select.enabled)")
        return 0
    from alpha import fit
    doc = fit.load_weights()
    if not doc:
        fit.main()
        doc = fit.load_weights()
    w = pd.Series(doc["weights"])
    syms = sorted(f.stem for f in (APP / "data" / "intraday").glob("*.parquet"))
    day = datetime.now().strftime("%Y-%m-%d")
    S = preopen_scores(syms, day, w)
    pinned = [str(s).upper() for s in pol["pinned"]] + open_positions()
    chosen = select(S, pinned, int(pol["size"]), float(pol["min_adv_crore"]))
    old = []
    if WATCHLIST.exists():
        old = [r["Symbol"] for r in csv.DictReader(open(WATCHLIST))]
    added = [s for s in chosen if s not in old]
    removed = [s for s in old if s not in chosen]
    top = S.score.sort_values()
    print(f"alpha watchlist {day}: {len(chosen)} stocks ({len(added)} in, {len(removed)} out); "
          f"strongest long {', '.join(top.index[::-1][:5])}; short {', '.join(top.index[:5])}")
    if "--dry-run" in sys.argv:
        print("dry run: watchlist.csv not written. In:", added, "Out:", removed)
        return 0
    n = write_watchlist(chosen)
    (APP / "data" / "alpha" / f"watchlist_{day}.json").write_text(json.dumps(
        {"chosen": chosen, "added": added, "removed": removed, "pinned": pinned,
         "scores": {s: round(float(S.score[s]), 6) for s in chosen if s in S.index}}, indent=1))
    print(f"watchlist.csv written: {n} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
