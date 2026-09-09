"""Bar-accurate outcome labelling.

The previous resolver sampled the last traded price once every 60 seconds, so
any stop touched and recovered inside a minute was missed and stop-hit rates
were systematically understated. This uses the 5-minute bar's high/low, and
requires a move to clear round-trip costs before it counts as correct.
"""
from __future__ import annotations

import pandas as pd


def label_outcome(bars: pd.DataFrame, entry_ts: int, entry: float, stop: float,
                  target: float, bias: str, horizon_min: int,
                  cost_pct: float) -> dict:
    start = pd.Timestamp(entry_ts, unit="s")
    end = start + pd.Timedelta(minutes=horizon_min)
    w = bars[(bars["timestamp"] >= start) & (bars["timestamp"] <= end)]

    result = {"hit_stop": False, "hit_target": False, "outcome": "TIMEOUT",
              "pnl_pct": 0.0, "directional_correct": False, "r_multiple": 0.0,
              "bars_seen": int(len(w))}
    if w.empty or entry <= 0:
        result["outcome"] = "NO_DATA"
        return result

    long = bias.upper() == "LONG"
    for _, b in w.iterrows():
        stop_hit = b["low"] <= stop if long else b["high"] >= stop
        tgt_hit = b["high"] >= target if long else b["low"] <= target
        if stop_hit and tgt_hit:
            # Both touched in one bar — assume the adverse side first.
            result.update(hit_stop=True, outcome="STOP")
            break
        if stop_hit:
            result.update(hit_stop=True, outcome="STOP")
            break
        if tgt_hit:
            result.update(hit_target=True, outcome="TARGET")
            break

    if result["outcome"] == "STOP":
        exit_px = stop
    elif result["outcome"] == "TARGET":
        exit_px = target
    else:
        exit_px = float(w["close"].iloc[-1])

    gross = ((exit_px - entry) / entry * 100.0) if long else ((entry - exit_px) / entry * 100.0)
    result["pnl_pct"] = round(gross, 4)
    result["directional_correct"] = bool(gross > cost_pct)

    risk = abs(entry - stop)
    if risk > 0:
        result["r_multiple"] = round(((exit_px - entry) if long else (entry - exit_px)) / risk, 3)
    return result
