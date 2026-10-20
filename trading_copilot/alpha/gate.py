"""Policy v7: the alpha score picks the entries; the v5 composite runs beside it (logged only).

apply() takes the composite's math_setup and returns the one the gatekeeper acts on:
  - alpha rank in the top `select_frac` -> LONG; in the bottom `select_frac` -> SHORT, with the
    researched geometry: entry at the price, stop STOP_ATR x ATR15 (x the time-of-day factor),
    target TARGET_R x risk (ALPHA_RESEARCH.md);
  - otherwise not a setup ("ALPHA_NOT_SELECTED");
  - ranks older than `max_age_min` or missing -> no entry ("ALPHA_STALE"): never trade blind.
  - v7.1: the stop multiple is `stop_atr` from the policy (2.5; research/geometry_test.py);
    the model's training label stays at STOP_ATR (1.5).
The composite's own numbers stay in the setup as v5_composite / v5_bias / v5_rejection, for the
feature log and the exit rules that were tested with it (thesis exit, fade to breakeven).
An open position (user_position) keeps the composite's setup: alpha only decides entries.
"""
from __future__ import annotations

import json
import time
from functools import lru_cache
from pathlib import Path

DEFAULTS = {"select_frac": 0.10, "max_age_min": 12.0}


def live_path() -> Path:
    from paths import DATA_DIR
    return DATA_DIR / "alpha" / "live.json"


@lru_cache(maxsize=2)
def _read(mtime: float) -> dict:
    try:
        return json.loads(live_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def live_scores() -> dict:
    try:
        return _read(live_path().stat().st_mtime)
    except OSError:
        return {}


def config() -> dict | None:
    """conviction.alpha from the policy in force; None when the alpha score is not the entry scorer."""
    try:
        from core.policy_config import load_policy
        cfg = load_policy().conviction.get("alpha")
    except Exception:
        return None
    if not cfg or not cfg.get("entry_scorer"):
        return None
    return {**DEFAULTS, **cfg}


def _tod(symbol: str) -> float:
    try:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from core.tod_vol import factor
        return factor(symbol, datetime.now(ZoneInfo("Asia/Kolkata")))
    except Exception:
        return 1.0


def apply(math_setup: dict, payload: dict, symbol: str, cfg: dict, live: dict | None = None,
          now: float | None = None, tod: float | None = None) -> dict:
    from alpha.core import STOP_ATR, TARGET_R
    from core.symbols import base_symbol
    sym = base_symbol(symbol)
    live = live_scores() if live is None else live
    now = time.time() if now is None else now
    out = dict(math_setup or {})
    out["v5_composite"] = (math_setup or {}).get("composite_score")
    out["v5_bias"] = (math_setup or {}).get("directional_bias")
    out["v5_rejection"] = (math_setup or {}).get("rejection_reason")
    out["scorer"] = "ALPHA"
    fresh = live.get("ts") and now - float(live["ts"]) <= cfg["max_age_min"] * 60
    entry = (live.get("scores") or {}).get(sym) if fresh else None
    out["alpha"] = entry

    def reject(reason):
        out.update(setup_rejected=True, rejection_reason=reason, directional_bias="NEUTRAL",
                   composite_score=(entry or {}).get("strength", 0.0), execution_geometry=None,
                   expectancy_matrix=None)
        return out

    if not fresh:
        return reject("ALPHA_STALE")
    if not entry:
        return reject("ALPHA_NO_SCORE")
    rank = float(entry["rank"])
    q = float(cfg["select_frac"])
    side = "LONG" if rank > 1 - q else ("SHORT" if rank <= q else None)
    if side is None:
        return reject("ALPHA_NOT_SELECTED")
    try:
        ltp = float(payload.get("ltp") or 0.0)
        atr = float(payload.get("atr_15m") or 0.0)
    except (TypeError, ValueError):
        ltp = atr = 0.0
    if ltp <= 0 or atr <= 0:
        return reject("ALPHA_NO_GEOMETRY")
    d = float(cfg.get("stop_atr") or STOP_ATR) * atr * (tod if tod is not None else _tod(sym))
    sg = 1.0 if side == "LONG" else -1.0
    stop, target = ltp - sg * d, ltp + sg * TARGET_R * d
    out.update(setup_rejected=False, rejection_reason=None, directional_bias=side,
               composite_score=round(float(entry["strength"]), 4),
               execution_geometry={"calculated_entry": round(ltp, 2), "padded_stop": round(stop, 2),
                                   "calculated_target": round(target, 2), "effective_risk": round(d, 4),
                                   "effective_reward": round(TARGET_R * d, 4)},
               expectancy_matrix={"expected_r": cfg.get("edge_r"), "reward_risk": TARGET_R,
                                  "implied_probability": None, "statistical_edge": None,
                                  "calibration_status": "ALPHA_RANK",
                                  "alpha_rank": rank, "alpha_time": live.get("time")})
    return out
