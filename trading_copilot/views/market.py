"""The Market tab's data (GET /api/market/summary and friends).

Pure: api_server reads the feed state, the flow file, the news state and the
daily NIFTY file, and these functions shape them. A figure that is not known
stays None -- the page drops that clause or draws "—", never a zero.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

_IST = ZoneInfo("Asia/Kolkata")

NIFTY_KEY = "NSE_INDEX|Nifty 50"
_INDEX_FRESH_S = 120            # an index price older than this is not "live"


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _pos(v):
    f = _num(v)
    return f if f and f > 0 else None


def change_pct(ltp, prev):
    """Percent change from the previous close, or None when either is unknown."""
    a, b = _pos(ltp), _pos(prev)
    return round((a - b) / b * 100, 4) if a and b else None


def bare(key: str) -> str:
    """'NSE_EQ|SAIL-EQ' -> 'SAIL'."""
    return str(key).split("|")[-1].split("-")[0]


# ------------------------------------------------------------------ NIFTY
def nifty(state: dict | None, daily_last: dict | None, now: float) -> dict:
    """The index today. From the feed when it streams the index (F1); until
    then, the last daily close from NIFTY50_1D.parquet, labelled as such."""
    st = state or {}
    ltp = _pos(st.get("ltp"))
    ts = _num(st.get("last_tick_ts"))
    age = round(now - ts, 1) if ts else None
    prev = _pos(st.get("prev_close"))
    last_day = daily_last or {}
    if not prev and last_day.get("close") and last_day.get("date") and last_day["date"] < _ist_day(now):
        prev = _pos(last_day["close"])            # yesterday's close is today's previous close
    if ltp:
        return {"source": "feed", "ltp": ltp, "prev_close": prev, "change": round(ltp - prev, 2) if prev else None,
                "change_pct": change_pct(ltp, prev), "open": _pos(st.get("day_open")), "high": _pos(st.get("day_high")),
                "low": _pos(st.get("day_low")), "age_s": age, "stale": age is None or age > _INDEX_FRESH_S,
                "pcr": _pos(st.get("stock_pcr"))}
    return {"source": "daily" if last_day.get("close") else None, "ltp": None, "prev_close": prev,
            "last_close": _pos(last_day.get("close")), "last_close_date": last_day.get("date"),
            "change": None, "change_pct": None, "open": None, "high": None, "low": None, "age_s": None,
            "stale": True, "pcr": _pos(st.get("stock_pcr"))}


def _ist_day(ts: float) -> str:
    return datetime.fromtimestamp(ts, _IST).strftime("%Y-%m-%d")


INDEX_RANGES = {"1W": 7, "1M": 31, "1Y": 366, "5Y": 5 * 366}


def index_history(rows: list, rng: str, today: date) -> dict:
    """Daily closes for the phone's NIFTY range control. rows: [{date, close}]
    oldest first (from NIFTY50_1D.parquet)."""
    days = INDEX_RANGES.get(rng)
    if days is None:
        raise ValueError(f"Unknown range {rng!r}. Use one of {', '.join(INDEX_RANGES)}.")
    start = (today - timedelta(days=days)).isoformat()
    pts = [{"date": r["date"], "close": _num(r["close"])} for r in rows if r.get("date", "") >= start and _pos(r.get("close"))]
    first, last = (pts[0]["close"], pts[-1]["close"]) if pts else (None, None)
    return {"range": rng, "points": pts, "first": first, "last": last,
            "change_pct": change_pct(last, first) if pts else None}


# -------------------------------------------------------------- breadth, flows
def breadth(flow_state: dict | None, ratio: float | None, source: str | None) -> dict:
    """A/D for the waffle. The counts come with the NSE ratio (macro worker);
    the watchlist proxy has only a ratio, and says so."""
    counts = (flow_state or {}).get("breadth") if source == "NIFTY_50" else None
    out = {"ratio": _num(ratio), "source": source, "advances": None, "declines": None, "unchanged": None}
    if isinstance(counts, dict):
        out.update({k: int(counts[k]) if _num(counts.get(k)) is not None else None
                    for k in ("advances", "declines", "unchanged")})
    return out


def flows(flow_state: dict | None, days: int = 10) -> dict:
    """The latest FII/DII net (₹ Cr) with its date, and the last `days` sessions."""
    fs = flow_state or {}
    hist = [h for h in (fs.get("flow_history") or []) if isinstance(h, dict) and h.get("date")][-days:]
    latest = {"date": fs.get("date") if fs.get("date") not in (None, "N/A") else None,
              "fii_net": _num(fs.get("fii_net")), "dii_net": _num(fs.get("dii_net"))}
    if hist and not latest["date"]:
        latest["date"] = hist[-1]["date"]
    return {"latest": latest, "history": [{"date": h["date"], "fii_net": _num(h.get("fii_net")),
                                           "dii_net": _num(h.get("dii_net"))} for h in hist]}


# ------------------------------------------------------------ the watchlist
SECTOR_NAMES = {
    "ADANI": "Adani group", "POWER_CAPGOODS": "Power and capital goods", "PSU_METALS_INFRA": "Metals and infra",
    "FINANCIALS": "Financials", "TELECOM": "Telecom", "IT": "IT", "HEALTHCARE": "Health", "SHIPPING": "Shipping",
    "EV": "Auto", "ENERGY": "Energy",
}


def stocks(states: dict) -> list:
    """[{symbol, ltp, change_pct, obi}] for the watchlist (indices excluded)."""
    out = []
    for key, st in (states or {}).items():
        if str(key).startswith("NSE_INDEX|") or not isinstance(st, dict):
            continue
        out.append({"symbol": bare(st.get("symbol") or key), "ltp": _pos(st.get("ltp")),
                    "change_pct": change_pct(st.get("ltp"), st.get("prev_close")), "obi": _num(st.get("obi"))})
    return sorted(out, key=lambda s: s["symbol"])


def sectors(rows: list, clusters: dict) -> list:
    """The watchlist grouped by sector, strongest average first; sectors of one
    stock share an "Others" row, last. Tiles run strongest to weakest.
    clusters: {symbol: cluster_id}, as core.risk.load_clusters returns it."""
    member = {str(k).upper(): str(v) for k, v in (clusters or {}).items()}
    groups: dict[str, list] = {}
    for r in rows:
        if r["change_pct"] is None:
            continue
        groups.setdefault(member.get(r["symbol"].upper(), r["symbol"]), []).append(r)
    main, others = [], []
    for cid, members in groups.items():
        if len(members) > 1:
            main.append((SECTOR_NAMES.get(cid, cid.replace("_", " ").title()), members))
        else:
            others.extend(members)
    avg = lambda ms: sum(m["change_pct"] for m in ms) / len(ms)   # noqa: E731
    main.sort(key=lambda g: avg(g[1]), reverse=True)
    if others:
        main.append(("Others", others))
    return [{"name": name, "avg": round(avg(ms), 4), "count": len(ms),
             "tiles": [{"symbol": m["symbol"], "change_pct": m["change_pct"]}
                       for m in sorted(ms, key=lambda m: m["change_pct"], reverse=True)]}
            for name, ms in main]


def quadrant(rows: list) -> list:
    """Order book (OBI) against the day's change, one point per stock.
    'absorbing' = buyers lead the book while the price falls."""
    return [{"symbol": r["symbol"], "obi": r["obi"], "change_pct": r["change_pct"],
             "absorbing": bool(r["obi"] > 0 and r["change_pct"] < 0)}
            for r in rows if r["obi"] is not None and r["change_pct"] is not None]


def wire(catalyst_cache: dict, watchlist: set | None = None, limit: int = 12) -> list:
    """Headlines from the news cache, one line each, watchlist stocks only.
    The cache carries no times or tone, so none are shown."""
    out, seen = [], set()
    for sym, entry in sorted((catalyst_cache or {}).items()):
        if watchlist is not None and sym.upper() not in watchlist:
            continue
        for n in (entry or {}).get("raw_news") or []:
            h = (n or {}).get("headline")
            if h and (sym, h) not in seen:
                seen.add((sym, h))
                out.append({"symbol": sym, "headline": h, "summary": (n or {}).get("summary") or ""})
                break                                     # the newest headline per stock
    return out[:limit]


def news_status(news_state: dict | None) -> dict:
    ns = news_state or {}
    return {"active": bool(ns.get("is_active")), "interval": _num(ns.get("interval")),
            "last_fetch": _num(ns.get("last_fetch_time")) or None, "model": ns.get("model"),
            "reachable": bool(ns)}


def context(ctx: dict | None) -> dict | None:
    """The AI's read of global news: sentiment word, text, and when."""
    if not ctx or not ctx.get("summary"):
        return None
    return {"sentiment": ctx.get("sentiment"), "summary": ctx.get("summary"), "ts": _num(ctx.get("timestamp"))}


def sparks(bars_by_symbol: dict) -> dict:
    """{symbol: [close, ...]} for today's bars -- the board's sparklines."""
    out = {}
    for sym, bars in (bars_by_symbol or {}).items():
        rows = [b for b in bars or [] if _pos(b.get("close"))]
        if not rows:
            out[sym] = []
            continue
        day = str(rows[-1].get("timestamp", ""))[:10]
        out[sym] = [round(float(b["close"]), 4) for b in rows if str(b.get("timestamp", ""))[:10] == day]
    return out
