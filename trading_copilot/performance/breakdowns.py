"""Where the account wins and where it bleeds: per-bucket stats, n always
attached. A bucket below metrics_min_n.bucket reports its sums (facts) but
no rates."""
from __future__ import annotations

from datetime import datetime
from statistics import mean

from performance.metrics import fact, gated
from performance.scope import IST

SCORE_EDGES = (0.1, 0.2, 0.3, 0.4, 0.5)


def score_bucket(composite) -> str | None:
    """|composite| in 0.1 steps; the sign is the direction, not the strength."""
    try:
        c = abs(float(composite))
    except (TypeError, ValueError):
        return None
    lo = 0.0
    for hi in SCORE_EDGES:
        if c < hi:
            return f"{lo:.1f}-{hi:.1f}"
        lo = hi
    return f"{SCORE_EDGES[-1]:.1f}+"


def _ctx(key):
    return lambda t: (t.get("context") or {}).get(key)


DIMENSIONS = {
    "regime": _ctx("regime"),
    "session_phase": _ctx("session_phase"),
    "symbol": lambda t: t.get("symbol"),
    "cluster": lambda t: t.get("cluster"),
    "direction": lambda t: t.get("side"),
    "exit_reason": lambda t: t.get("reason"),
    "score_bucket": lambda t: score_bucket((t.get("context") or {}).get("composite")),
    "verdict": _ctx("verdict"),
    "hour": lambda t: datetime.fromtimestamp(t["opened_ts"], IST).strftime("%H:00"),
    "config_version": lambda t: t.get("config_version"),
}
ORDINAL = {"score_bucket", "hour", "config_version"}


def bucket_stats(trades: list, min_n: int) -> dict:
    n = len(trades)
    nets = [t["net"] for t in trades]
    rs = [t["r_net"] for t in trades if t.get("r_net") is not None]
    return {
        "n": n,
        "net": fact(sum(nets), n),
        "gross": fact(sum(t["gross"] for t in trades), n),
        "costs": fact(sum(t["costs"]["total"] for t in trades), n),
        "win_rate": gated(sum(v > 0 for v in nets) / n * 100.0 if n else None, n, min_n),
        "expectancy": gated(mean(nets) if nets else None, n, min_n),
        "expectancy_r": gated(mean(rs) if rs else None, len(rs), min_n),
        "avg_hold_min": fact(mean(t["hold_min"] for t in trades) if trades else None, n),
    }


def breakdown(trades: list, by: str, min_n: int) -> list[dict]:
    """[{key, label, n, net, ...}]. Unknown values are kept as their own
    bucket (key None, label "unknown") -- never dropped, never guessed."""
    if by not in DIMENSIONS:
        raise ValueError(f"Unknown breakdown '{by}'. Use one of: {', '.join(DIMENSIONS)}.")
    fn, groups = DIMENSIONS[by], {}
    for t in trades:
        groups.setdefault(fn(t), []).append(t)
    rows = [{"key": k, "label": "unknown" if k is None else str(k), **bucket_stats(v, min_n)}
            for k, v in groups.items()]
    if by in ORDINAL:
        rows.sort(key=lambda r: (r["key"] is None, r["key"] if r["key"] is not None else 0))
    else:
        rows.sort(key=lambda r: (-r["n"], r["label"]))
    return rows


def all_breakdowns(trades: list, min_n: int) -> dict:
    return {by: breakdown(trades, by, min_n) for by in DIMENSIONS}
