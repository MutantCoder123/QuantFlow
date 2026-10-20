"""Holes in a stock's 5-minute bars, and filling them from 1-minute candles.

The engine builds its 5-minute bars (ltf_df) only from live ticks, so any time
the feed was not running leaves a hole: on 2026-10-08 the PC was in standby
08:46-13:14 and NBCC's bars went from 10-07 14:45 straight to 10-08 13:10.
Five entries at 13:15-13:19 were scored on about three bars of the day
(VWAP, ATR, regime all wrong).

- missing_today(ltf_df, upto): today's session slots before `upto` with no bar.
- longest_run(slots): the longest stretch of consecutive missing slots.
- bars_from_minutes(minutes): 1-minute candles (ts, o, h, l, c, v) -> 5-minute bars
  stamped at their start, naive IST, like ltf_df.
- fill(ltf_df, bars, before): ltf_df plus every bar of `bars` it lacks, time order.

Pure; the feed fills today from Upstox's intraday candles, the engine fills
earlier sessions from the local 1-minute store (data/intraday) at start.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta

import pandas as pd

BAR = timedelta(minutes=5)
OPEN, LAST_SLOT = time(9, 15), time(15, 25)
GAP_RUN = 2          # this many consecutive missing slots blocks new entries (10 min)


def session_slots(day, upto: datetime | None = None) -> list[datetime]:
    """Bar starts of `day` from 09:15 to 15:25, only those before `upto`."""
    t = datetime.combine(day, OPEN)
    end = datetime.combine(day, LAST_SLOT)
    out = []
    while t <= end and (upto is None or t < upto):
        out.append(t)
        t += BAR
    return out


def _stamps(ltf_df) -> set:
    if ltf_df is None or len(ltf_df) == 0 or "timestamp" not in ltf_df:
        return set()
    return set(pd.to_datetime(ltf_df["timestamp"]).dt.to_pydatetime())


def missing_today(ltf_df, upto: datetime) -> list[datetime]:
    """Today's completed slots (start < `upto`, the live bar's start) with no bar."""
    have = _stamps(ltf_df.tail(120) if ltf_df is not None else None)
    return [s for s in session_slots(upto.date(), upto) if s not in have]


def longest_run(slots: list[datetime]) -> int:
    best = run = 0
    prev = None
    for s in sorted(slots):
        run = run + 1 if prev is not None and s - prev == BAR else 1
        best, prev = max(best, run), s
    return best


def bars_from_minutes(minutes: pd.DataFrame) -> pd.DataFrame:
    """1-minute candles (columns ts, o, h, l, c, v) -> 5-minute OHLCV bars."""
    cols = ["timestamp", "open", "high", "low", "close", "volume", "oi"]
    if minutes is None or len(minutes) == 0:
        return pd.DataFrame(columns=cols)
    m = minutes.copy()
    m["ts"] = pd.to_datetime(m["ts"])
    m = m.set_index("ts").sort_index()
    b = m.resample("5min").agg({"o": "first", "h": "max", "l": "min", "c": "last", "v": "sum"}).dropna()
    b = b[(b.index.time >= OPEN) & (b.index.time <= LAST_SLOT)]
    out = pd.DataFrame({"timestamp": b.index, "open": b.o.values, "high": b.h.values, "low": b.l.values,
                        "close": b.c.values, "volume": b.v.astype(float).values, "oi": 0.0})
    return out.reset_index(drop=True)


def fill(ltf_df: pd.DataFrame, bars: pd.DataFrame, before: datetime | None = None) -> tuple[pd.DataFrame, int]:
    """(ltf_df with every bar of `bars` it lacks, bars added). Only bars starting
    before `before` (the live bar) are taken; live bars are never replaced."""
    if bars is None or len(bars) == 0:
        return ltf_df, 0
    b = bars.copy()
    b["timestamp"] = pd.to_datetime(b["timestamp"])
    if before is not None:
        b = b[b["timestamp"] < before]
    b = b[~b["timestamp"].isin(_stamps(ltf_df))]
    if b.empty:
        return ltf_df, 0
    cols = list(ltf_df.columns) if ltf_df is not None and len(ltf_df.columns) else list(b.columns)
    for c in cols:
        if c not in b:
            b[c] = 0.0
    base = ltf_df.copy() if ltf_df is not None else pd.DataFrame(columns=cols)
    base["timestamp"] = pd.to_datetime(base["timestamp"])
    out = pd.concat([base, b[cols]], ignore_index=True).sort_values("timestamp", kind="stable")
    return out.reset_index(drop=True), len(b)


def minutes_from_upstox(candles: list) -> pd.DataFrame:
    """Upstox candles [iso_ts, o, h, l, c, vol, ...] -> (ts naive IST, o, h, l, c, v)."""
    if not candles:
        return pd.DataFrame(columns=["ts", "o", "h", "l", "c", "v"])
    df = pd.DataFrame([c[:6] for c in candles], columns=["ts", "o", "h", "l", "c", "v"])
    ts = pd.to_datetime(df["ts"])
    if ts.dt.tz is not None:
        ts = ts.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    df["ts"] = ts
    return df
