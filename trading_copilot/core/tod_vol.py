"""How wide a stock's 15-minute bars usually are at each time of day (policy v6).

ATR15 is an average of the last 14 bars. At 09:45-10:30 those bars are mostly
yesterday afternoon's -- quieter than the morning -- so a stop sized on ATR15
is too tight in the morning (entries before 10:30: 29 trades, 4 wins,
-61.8k). `factor(symbol, now)` = the usual width of the current slot / the
usual width of the 14 slots the ATR was measured on, from the last 20
sessions; never below 1 (it only widens), at most MAX_FACTOR.

data/tod_vol.json: {"built": day, "slots": [...], "stocks": {SYM: {slot: ratio}}},
ratio = median (high-low)/close of that slot / the median over all slots.
Built by build() from the local 1-minute store (data/intraday), after the close.
"""
from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path

SESSIONS = 20
ATR_BARS = 14
MAX_FACTOR = 2.5
SLOTS = [f"{h:02d}:{m:02d}" for h in range(9, 16) for m in (0, 15, 30, 45)
         if (9, 15) <= (h, m) <= (15, 15)]


def _path() -> Path:
    from paths import DATA_DIR
    return DATA_DIR / "tod_vol.json"


@lru_cache(maxsize=1)
def _load(mtime: float | None) -> dict:
    try:
        return json.loads(_path().read_text(encoding="utf-8")).get("stocks", {})
    except (OSError, ValueError):
        return {}


def table() -> dict:
    try:
        mtime = _path().stat().st_mtime
    except OSError:
        mtime = None
    return _load(mtime)


def slot_of(now: datetime) -> str:
    m = (now.hour * 60 + now.minute) // 15 * 15
    return f"{m // 60:02d}:{m % 60:02d}"


def factor(symbol: str, now: datetime, ratios: dict | None = None) -> float:
    """>= 1.0: how much wider the current slot runs than the bars behind ATR15."""
    r = ratios if ratios is not None else table().get(symbol)
    if not r:
        return 1.0
    s = slot_of(now)
    if s not in SLOTS or s not in r:
        return 1.0
    i = SLOTS.index(s)
    behind = [SLOTS[(i - k) % len(SLOTS)] for k in range(1, ATR_BARS + 1)]   # wraps to yesterday's close
    base = [r[x] for x in behind if x in r]
    if not base:
        return 1.0
    f = r[s] / (sum(base) / len(base))
    return round(min(MAX_FACTOR, max(1.0, f)), 3)


def ratios_from_minutes(m) -> dict:
    """{slot: ratio} from 1-minute candles (columns ts, h, l, c) of the last SESSIONS days."""
    import pandas as pd
    m = m.copy()
    m["ts"] = pd.to_datetime(m["ts"])
    days = sorted(m.ts.dt.date.unique())[-SESSIONS:]
    m = m[m.ts.dt.date.isin(days)]
    b = m.set_index("ts").resample("15min").agg({"h": "max", "l": "min", "c": "last"}).dropna()
    b["w"] = (b.h - b.l) / b.c
    b["slot"] = b.index.strftime("%H:%M")
    med = b.groupby("slot").w.median()
    med = med[med.index.isin(SLOTS)]
    overall = b.w.median()
    if not overall or overall <= 0:
        return {}
    return {s: round(float(v / overall), 4) for s, v in med.items()}


def build(symbols=None) -> Path:
    import pandas as pd
    from paths import DATA_DIR
    store = DATA_DIR / "intraday"
    files = sorted(store.glob("*.parquet"))
    out = {}
    for f in files:
        if symbols and f.stem not in symbols:
            continue
        try:
            out[f.stem] = ratios_from_minutes(pd.read_parquet(f, columns=["ts", "h", "l", "c"]))
        except Exception:
            continue
    p = _path()
    from core.fileio import write_json_atomic
    write_json_atomic(str(p), {"built": datetime.now().strftime("%Y-%m-%d %H:%M"), "slots": SLOTS, "stocks": out})
    return p

