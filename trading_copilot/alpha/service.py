"""The alpha score, live: rank every F&O stock after each 5-minute bar (a run.py service).

At each bar close from 09:45 to 13:40 (+20 s), this:
  - fetches today's 1-minute candles for the stored F&O universe (Upstox, ISIN keys);
  - appends them as today to the last 30 sessions of the local store;
  - computes the 24 factors (alpha.core) and scores them with data/alpha/weights.json (refit
    nightly; fitted here if missing).
It writes:
  - data/alpha/live.json, the latest ranks, read by alpha.gate for entries (policy v7);
  - data/alpha/alpha_<day>.jsonl, every score, for the after-close comparison.
Outside market hours it sleeps. It never trades.

usage: python trading_copilot/alpha/service.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
APP = HERE.parent
for p in (APP, APP / "data_services"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from alpha import core  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
OUT = APP / "data" / "alpha"
HISTORY_DAYS = 30
FIRST_BAR, LAST_BAR = 6, 53            # decisions at 09:45 .. 13:40 (the entry window)
log = logging.getLogger("alpha")


def live_path() -> Path:
    return OUT / "live.json"


def bar_time(day: str, k: int) -> datetime:
    return datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=IST) + timedelta(minutes=9 * 60 + 15 + 5 * k)


def instrument_keys(syms) -> dict:
    import scrip_master_engine
    out = {}
    for s in syms:
        try:
            k = scrip_master_engine.get_instrument_key(str(s))
            if k:
                out[str(s)] = k
        except Exception:
            pass
    return out


async def fetch_candles(keymap: dict, token: str) -> dict:
    import aiohttp
    sem = asyncio.Semaphore(8)
    out: dict = {}

    async def one(session, sym, key):
        url = f"https://api.upstox.com/v3/historical-candle/intraday/{key}/minutes/1"
        async with sem:
            for _ in range(3):
                try:
                    async with session.get(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                                           timeout=aiohttp.ClientTimeout(total=15)) as r:
                        body = await r.json(content_type=None)
                    out[sym] = (body.get("data") or {}).get("candles") or []
                    return
                except Exception:
                    await asyncio.sleep(1)
    async with aiohttp.ClientSession() as session:
        await asyncio.gather(*(one(session, s, k) for s, k in keymap.items()))
    return out


def today_arrays(syms, candles: dict, upto: datetime) -> dict:
    """(S, 75) 5-minute o/h/l/c/v for today from the 1-minute candles that ended by `upto`."""
    S = len(syms)
    arr = {k: np.full((S, core.NB), np.nan, dtype=np.float32) for k in "ohlcv"}
    cut = pd.Timestamp(upto.astimezone(IST).replace(tzinfo=None))
    for i, s in enumerate(syms):
        c = candles.get(str(s)) or []
        if not c:
            continue
        m = pd.DataFrame([x[:6] for x in c], columns=["ts", "o", "h", "l", "c", "v"])
        m["ts"] = pd.to_datetime(m.ts)
        if m.ts.dt.tz is not None:
            m["ts"] = m.ts.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        m = m[m.ts + pd.Timedelta(minutes=1) <= cut]
        mins = m.ts.dt.hour * 60 + m.ts.dt.minute - (9 * 60 + 15)
        m = m[(mins >= 0) & (mins < core.NB * 5)]
        if m.empty:
            continue
        m = m.assign(j=((m.ts.dt.hour * 60 + m.ts.dt.minute - (9 * 60 + 15)) // 5).astype(int)).sort_values("ts")
        g = m.groupby("j")
        b = pd.DataFrame({"o": g.o.first(), "h": g.h.max(), "l": g.l.min(), "c": g.c.last(), "v": g.v.sum()})
        for k in "ohlcv":
            arr[k][i, b.index.values] = b[k].values.astype(np.float32)
    return core.fill_bars(arr)


def with_today(base: dict, today: dict, day: str) -> dict:
    """The stored panel (last HISTORY_DAYS) with today appended, daily factors recomputed for it."""
    syms = [str(s) for s in base["syms"]]
    days = [str(d) for d in base["days"][-HISTORY_DAYS:] if str(d) != day] + [day]
    n_hist = len(days) - 1
    P = {"syms": np.array(syms), "days": np.array(days)}
    for k in "ohlcv":
        P[k] = np.concatenate([base[k][:, -n_hist:, :], today[k][:, None, :]], axis=1)
    for k, v in core.daily_block(syms, [pd.Timestamp(d) for d in days]).items():
        P[f"d_{k}"] = v
    return P


def score_bar(P: dict, k: int, w: pd.Series) -> pd.DataFrame:
    X = core.features(P, live=True, slots=[k])
    X = X[X.d == len(P["days"]) - 1].copy()
    if len(X) < 50:
        return X.iloc[0:0]
    X["score"] = core.score(X, w)
    X["rank"] = X.score.rank(pct=True)
    X["strength"] = (X["rank"] - 0.5) * 2.0            # -1 .. +1, the composite's scale
    X["symbol"] = P["syms"][X.s.values]
    return X


def write(X: pd.DataFrame, day: str, k: int, weights_day: str) -> None:
    from core.fileio import write_json_atomic
    t = bar_time(day, k)
    doc = {"ts": t.timestamp(), "time": t.strftime("%H:%M"), "day": day, "k": k, "weights_through": weights_day,
           "scores": {r.symbol: {"score": round(float(r.score), 6), "rank": round(float(r.rank), 4),
                                 "strength": round(float(r.strength), 4)} for r in X.itertuples(index=False)}}
    write_json_atomic(str(live_path()), doc)
    with open(OUT / f"alpha_{day}.jsonl", "a", encoding="utf-8") as f:
        for r in X.itertuples(index=False):
            f.write(json.dumps({"ts": doc["ts"], "time": doc["time"], "k": k, "symbol": r.symbol,
                                "score": round(float(r.score), 6), "rank": round(float(r.rank), 4),
                                "factors": {c: (None if not np.isfinite(getattr(r, c)) else round(float(getattr(r, c)), 4))
                                            for c in core.FACTORS}}) + "\n")


def load_or_fit() -> tuple[pd.Series, str]:
    from alpha import fit
    doc = fit.load_weights()
    if not doc:
        fit.main()
        doc = fit.load_weights()
    return pd.Series(doc["weights"]), doc.get("fitted_through", "?")


def run_day(day: str) -> None:
    w, wday = load_or_fit()
    base = core.panel_from_store(last_days=HISTORY_DAYS)
    syms = [str(s) for s in base["syms"]]
    keymap = instrument_keys(syms)
    token = json.load(open(APP / "upstox_token.json"))["access_token"]
    log.info(f"alpha service {day}: {len(keymap)} stocks, weights fitted through {wday}")
    done = set()
    while True:
        now = datetime.now(IST)
        due = [k for k in range(FIRST_BAR, LAST_BAR + 1)
               if bar_time(day, k) + timedelta(seconds=20) <= now and k not in done]
        if not due:
            if now > bar_time(day, LAST_BAR) + timedelta(minutes=1):
                log.info("alpha service: entry window over for today")
                return
            time.sleep(5)
            continue
        k = max(due)                                          # catch up on the latest bar only
        done.update(due)
        t0 = time.time()
        try:
            candles = asyncio.run(fetch_candles(keymap, token))
            P = with_today(base, today_arrays(syms, candles, now), day)
            X = score_bar(P, k, w)
            if len(X):
                write(X, day, k, wday)
                top = X.sort_values("score")
                log.info(f"{bar_time(day, k):%H:%M} scored {len(X)} in {time.time() - t0:.0f}s | top: "
                         f"{', '.join(top.symbol.tail(3)[::-1])} | bottom: {', '.join(top.symbol.head(3))}")
            else:
                log.warning(f"{bar_time(day, k):%H:%M}: too few stocks with data; not written")
        except Exception as e:
            log.error(f"alpha bar {k} failed: {type(e).__name__}: {e}", exc_info=True)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    import warnings
    warnings.filterwarnings("ignore")
    OUT.mkdir(parents=True, exist_ok=True)
    finished = set()
    while True:
        now = datetime.now(IST)
        day = now.strftime("%Y-%m-%d")
        if day not in finished and now.weekday() < 5 and                 bar_time(day, FIRST_BAR) - timedelta(minutes=5) <= now <= bar_time(day, LAST_BAR) + timedelta(minutes=1):
            run_day(day)
            finished.add(day)
        time.sleep(30)


if __name__ == "__main__":
    main()
