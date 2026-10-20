"""Measured expected value per regime and score band (policy v6 ev_gate).

v5 admitted a setup on reward:risk >= 1.5 while uncalibrated. That rewards a
tight stop: the closer a level sits under the price, the better the ratio
looks, so the gate selected stops inside noise (median 0.73 ATR15; FUTURE_UPDATES
2.5). v6 asks instead: what did readings like this one -- same regime, same
|score| band -- earn when traded with v6's geometry, in R after friction?

data/<file> (policy conviction.ev_gate.file), fitted by
scripts/fit_ev_calibration.py:
  {"fitted_at", "days", "geometry": {...}, "regimes": {REGIME: [[lo, hi, mean_r, n], ...]}}
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


def _path(name: str) -> Path:
    from paths import DATA_DIR
    return DATA_DIR / name


@lru_cache(maxsize=4)
def _load(name: str, mtime: float | None) -> dict:
    try:
        return json.loads(_path(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def table(name: str) -> dict:
    try:
        mtime = _path(name).stat().st_mtime
    except OSError:
        mtime = None
    return _load(name, mtime)


def expected_r(regime: str, composite: float, cfg: dict, data: dict | None = None) -> tuple[float | None, int]:
    """(mean R after friction, readings behind it) for this regime and |score|;
    (None, n) when the band has fewer than cfg['min_n'] readings or none at all."""
    d = data if data is not None else table(cfg.get("file", "ev_calibration.json"))
    try:
        s = abs(float(composite))
    except (TypeError, ValueError):
        return None, 0
    for lo, hi, mean_r, n in (d.get("regimes") or {}).get(str(regime), []):
        if lo <= s < hi:
            return (float(mean_r) if n >= int(cfg.get("min_n", 100)) else None), int(n)
    return None, 0
