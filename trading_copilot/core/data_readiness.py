"""Is a stock's data complete enough to trade on?

A new position is considered only when every input the decision is built
from is actually there. On 2026-09-30 and 2026-10-01 stocks added to the
watchlist that week (BANKINDIA, DELHIVERY, GLENMARK, POLICYBZR) were scored
with no 52-week options history -- their derivatives component silently fell
to neutral -- because the options backfill had not reached them yet.

Exits are never gated on this: an open position must always be manageable.
Live freshness (data_age_s) is checked separately, by the stale-data shield.
"""
from __future__ import annotations

import math

# payload key -> what is missing, in words. "positive" keys must also be > 0.
REQUIRED = {
    "iv_percentile_52w": "52-week IV history",
    "pcr_percentile_52w": "52-week PCR history",
    "rolling_20d_poc_price": "20-day volume profile",
    "rolling_20d_value_area_high": "20-day value area",
    "rolling_20d_value_area_low": "20-day value area",
    "atr_15m": "15-minute ATR",
    "atr_1h": "1-hour ATR",
    "ema_21_1h": "1-hour trend average",
    "session_vwap": "session VWAP",
    "prev_close": "previous close",
    "adv_shares": "average daily volume",
}
POSITIVE = {"rolling_20d_poc_price", "rolling_20d_value_area_high", "rolling_20d_value_area_low",
            "atr_15m", "atr_1h", "ema_21_1h", "session_vwap", "prev_close", "adv_shares"}


def _absent(key: str, value) -> bool:
    if value is None:
        return True
    try:
        v = float(value)
    except (TypeError, ValueError):
        return True
    if math.isnan(v) or math.isinf(v):
        return True
    return key in POSITIVE and v <= 0


def missing(payload: dict | None) -> list[str]:
    """What is missing, in words, without repeats; [] when complete."""
    p = payload or {}
    out: list[str] = []
    for key, words in REQUIRED.items():
        if _absent(key, p.get(key)) and words not in out:
            out.append(words)
    # A hole in today's 5-minute bars (the feed was not running -- 2026-10-08 the PC
    # was in standby 08:46-13:14): VWAP, ATR and regime are wrong until the feed
    # fills it from the exchange's candles (core.bar_gaps).
    from core.bar_gaps import GAP_RUN
    try:
        gap = int(p.get("bars_gap_run") or 0)
    except (TypeError, ValueError):
        gap = 0
    if gap >= GAP_RUN:
        out.append(f"today's 5-minute bars ({gap * 5} min missing)")
    return out


def ready(payload: dict | None) -> bool:
    return not missing(payload)
