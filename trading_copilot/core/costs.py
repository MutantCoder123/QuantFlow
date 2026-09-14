"""Round-trip transaction-cost model (improved §4.3.2).

The expectancy math previously modelled only +/-0.1*ATR of slippage
(conviction_scorer.py) and no charges at all. Against a 1.5*ATR15m minimum
target -- often 0.3-0.5% on a mid-cap -- brokerage/STT/GST/stamp consume a
double-digit percentage of gross reward. Some currently-passing setups
should stop passing once this is in the denominator; that is the point.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml


@lru_cache(maxsize=4)
def load_costs(path: Path | None = None) -> dict:
    if path is None:
        from paths import CONFIG_DIR
        path = CONFIG_DIR / "costs.yaml"
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)["intraday_equity"]


def _leg_brokerage(notional: float, cfg: dict) -> float:
    return min(notional * cfg["brokerage_pct"] / 100.0, float(cfg["brokerage_cap"]))


def round_trip_cost_pct(entry: float, exit_px: float, cfg: dict) -> float:
    """Total buy+sell charges as a percentage of the entry notional.

    Uses cfg['ref_qty'] as the assumed order size purely so the Rs.20
    brokerage cap can make the cost-% realistically order-size dependent
    (large orders pay a capped brokerage, so a smaller %). Everything else
    is linear in notional.
    """
    if entry <= 0:
        return 0.0
    qty = float(cfg.get("ref_qty", 100))
    buy_notional = entry * qty
    sell_notional = exit_px * qty

    brokerage = _leg_brokerage(buy_notional, cfg) + _leg_brokerage(sell_notional, cfg)
    stt = sell_notional * cfg["stt_sell_pct"] / 100.0
    exchange_txn = (buy_notional + sell_notional) * cfg["exchange_txn_pct"] / 100.0
    sebi = (buy_notional + sell_notional) * cfg["sebi_pct"] / 100.0
    stamp = buy_notional * cfg["stamp_duty_buy_pct"] / 100.0
    gst = cfg["gst_pct"] / 100.0 * (brokerage + exchange_txn + sebi)

    total = brokerage + stt + exchange_txn + sebi + stamp + gst
    return total / buy_notional * 100.0


def net_reward(gross: float, entry: float, exit_px: float, spread: float, cfg: dict) -> float:
    """Gross per-share reward minus round-trip charges (in the same price
    units) minus the assumed spread paid crossing in and out."""
    cost_abs = entry * round_trip_cost_pct(entry, exit_px, cfg) / 100.0
    return gross - cost_abs - spread
