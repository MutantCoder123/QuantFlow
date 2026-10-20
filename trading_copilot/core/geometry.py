"""Stop and target geometry shared by the scorer (policy v6) and the paper
broker (policy v5.1): a stop out of the noise, a target worth the risk. Pure."""
from __future__ import annotations


def widen_geometry(entry: float, stop: float, target: float, bias: str, atr_eff: float,
                   floor_k: float, target_min_r: float) -> tuple[float, float]:
    """Policy v6: (stop, target) with the stop at least floor_k x atr_eff from the
    entry (a wider structure stop is kept) and the target at least target_min_r R
    away. floor_k / target_min_r 0 = unchanged (v5)."""
    sgn = 1.0 if bias == "LONG" else -1.0
    if floor_k > 0 and abs(entry - stop) < floor_k * atr_eff:
        stop = entry - sgn * floor_k * atr_eff
    if target_min_r > 0:
        d = abs(entry - stop)
        if abs(target - entry) < target_min_r * d:
            target = entry + sgn * target_min_r * d
    return stop, target
