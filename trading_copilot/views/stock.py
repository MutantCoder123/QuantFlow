"""One stock, for the Inspector: today's bars and the levels drawn over them."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

_IST = ZoneInfo("Asia/Kolkata")


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _parse_ts(raw) -> datetime | None:
    """Feed bar timestamps are naive IST strings ("2026-09-29 12:25:00")."""
    try:
        dt = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    return dt.replace(tzinfo=_IST) if dt.tzinfo is None else dt.astimezone(_IST)


def session_bars(raw_bars: list) -> dict:
    """The latest session's bars, oldest first, each with the session VWAP to
    that bar (typical price weighted by volume) and, when the feed kept it,
    the cumulative delta at the bar's close.

    A bar with no usable OHLC is dropped rather than drawn at zero; a session
    with no volume yet has no VWAP (None), not a price.
    """
    rows = []
    for b in raw_bars or []:
        ts = _parse_ts(b.get("timestamp"))
        o, h, l, c = (_num(b.get(k)) for k in ("open", "high", "low", "close"))
        if ts is None or None in (o, h, l, c):
            continue
        rows.append((ts, o, h, l, c, _num(b.get("volume")) or 0.0, _num(b.get("cvd"))))
    if not rows:
        return {"session_date": None, "bars": []}
    rows.sort(key=lambda r: r[0])
    day = rows[-1][0].date()
    out, pv, vol = [], 0.0, 0.0
    for ts, o, h, l, c, v, cvd in rows:
        if ts.date() != day:
            continue
        pv += (h + l + c) / 3.0 * v
        vol += v
        out.append({"ts": int(ts.timestamp()), "t": ts.strftime("%H:%M"), "o": o, "h": h, "l": l, "c": c,
                    "v": v, "vwap": round(pv / vol, 4) if vol > 0 else None, "cvd": cvd})
    return {"session_date": day.isoformat(), "bars": out}


def levels(state: dict) -> dict:
    """The reference levels the chart draws, from the live state; None when unknown.
    The value area and POC are the rolling 20-day ones the scorer reads."""
    s = state or {}
    pick = lambda *keys: next((v for v in (_num(s.get(k)) for k in keys) if v), None)  # noqa: E731
    return {
        "ltp": pick("ltp"), "prev_close": pick("prev_close"), "vwap": pick("session_vwap", "vwap_5m"),
        "poc": pick("rolling_20d_poc_price"), "va_high": pick("rolling_20d_value_area_high"),
        "va_low": pick("rolling_20d_value_area_low"), "max_pain": pick("max_pain_price"),
    }
