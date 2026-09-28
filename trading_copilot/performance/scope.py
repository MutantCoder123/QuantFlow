"""What a metric is computed over: a date range of the paper event log.

A Scope carries the trades that CLOSED inside the range, plus what the
capital-relative figures need from outside it -- the realised P&L banked
before the range began and the capital in force at any moment -- so that
changing capital (a SETTINGS event) never quietly rewrites history.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from paper.store import rebuild

IST = ZoneInfo("Asia/Kolkata")
RANGES = ("today", "5d", "1m", "all", "custom")
SESSION_OPEN_S = 9 * 3600 + 15 * 60


def ist_day(ts: float) -> str:
    return datetime.fromtimestamp(ts, IST).strftime("%Y-%m-%d")


def day_start_ts(day: str) -> float:
    return datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=IST).timestamp()


def bounds(rng: str, now: float, frm: str | None = None,
           to: str | None = None) -> tuple[str | None, str | None]:
    """(first_day, last_day) inclusive, IST. None means unbounded.
    Raises ValueError with a plain-language message on a bad range."""
    today = datetime.fromtimestamp(now, IST).date()
    if rng == "today":
        return today.isoformat(), today.isoformat()
    if rng == "5d":                              # the last 5 weekdays, today included
        d, seen = today, 0
        while True:
            if d.weekday() < 5:
                seen += 1
                if seen == 5:
                    break
            d -= timedelta(days=1)
        return d.isoformat(), today.isoformat()
    if rng == "1m":
        return (today - timedelta(days=30)).isoformat(), today.isoformat()
    if rng == "all":
        return None, None
    if rng == "custom":
        try:
            a = date.fromisoformat(frm) if frm else None
            b = date.fromisoformat(to) if to else None
        except ValueError:
            raise ValueError("Dates must look like 2026-09-28.")
        if a and b and a > b:
            raise ValueError("The start date is after the end date.")
        return (a.isoformat() if a else None), (b.isoformat() if b else None)
    raise ValueError(f"Unknown range '{rng}'. Use one of: {', '.join(RANGES)}.")


def _within(day: str, first: str | None, last: str | None) -> bool:
    return (first is None or day >= first) and (last is None or day <= last)


@dataclass
class Scope:
    first_day: str | None
    last_day: str | None
    trades: list                      # closed inside the range, in close order
    rejections: list
    equity: list                      # EQUITY snapshots inside the range
    settings_changes: list            # all of them (capital history needs every one)
    session_days: list                # IST days inside the range the engine logged anything
    prior_net: float                  # realised net banked before the range began
    current_capital: float
    all_trades: list = field(default_factory=list)

    def capital_at(self, ts: float) -> float:
        """Capital in force at `ts`: the `old` side of the first capital
        change after it, else today's capital."""
        for e in self.settings_changes:
            if e.get("ts", 0) > ts and "capital" in (e.get("old") or {}):
                return float(e["old"]["capital"])
        return float(self.current_capital)

    def contains(self, ts: float) -> bool:
        return _within(ist_day(ts), self.first_day, self.last_day)

    def start_ts(self) -> float | None:
        """The 09:15 open of the first day (so a capital change made before the
        open applies), else the first activity in an unbounded range."""
        if self.first_day:
            return day_start_ts(self.first_day) + SESSION_OPEN_S
        firsts = [t["opened_ts"] for t in self.trades] + [e["ts"] for e in self.equity]
        return min(firsts) if firsts else None


def select(events: list[dict], rng: str, now: float, current_capital: float,
           frm: str | None = None, to: str | None = None) -> Scope:
    first, last = bounds(rng, now, frm, to)
    state = rebuild(events)
    closed = sorted(state.closed, key=lambda t: t["closed_ts"])
    inside = [t for t in closed if _within(ist_day(t["closed_ts"]), first, last)]
    before = [t for t in closed if first is not None and ist_day(t["closed_ts"]) < first]
    days = sorted({ist_day(e["ts"]) for e in events if "ts" in e and _within(ist_day(e["ts"]), first, last)})
    return Scope(
        first_day=first, last_day=last, trades=inside,
        rejections=[r for r in state.rejections if _within(ist_day(r["ts"]), first, last)],
        equity=[e for e in state.equity if _within(ist_day(e["ts"]), first, last)],
        settings_changes=sorted(state.settings_changes, key=lambda e: e.get("ts", 0)),
        session_days=days, prior_net=sum(t["net"] for t in before),
        current_capital=float(current_capital), all_trades=closed,
    )
