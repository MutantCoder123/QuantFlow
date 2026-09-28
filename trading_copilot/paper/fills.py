"""Fill model and per-trade costs -- pure functions, conservative by design.

Assumptions (all surfaced in the UI):
  * entry and market exits pay `slippage_pct` against us;
  * a stop is filled at the stop, or at the bar's open if the bar gapped
    through it (the worse of the two);
  * a target is filled at the target -- never at a better price;
  * a bar that touches both stop and target is taken as a stop, since the
    order of the two inside the bar is unknown.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Bar:
    ts: float
    open: float
    high: float
    low: float
    close: float


def _f(x) -> float:
    return float(x)


def entry_fill(side: str, ltp, slippage_pct) -> float:
    s = _f(slippage_pct) / 100.0
    return _f(ltp) * (1 + s) if side == "LONG" else _f(ltp) * (1 - s)


def market_exit_fill(side: str, ltp, slippage_pct) -> float:
    s = _f(slippage_pct) / 100.0
    return _f(ltp) * (1 - s) if side == "LONG" else _f(ltp) * (1 + s)


def first_touch(side: str, stop, target, bars) -> tuple[str, float, float] | None:
    """(STOP|TARGET, fill price, bar ts) for the first bar, in time order,
    that touches the stop or the target; None if neither was touched."""
    stop, target = _f(stop), _f(target)
    for b in sorted(bars, key=lambda b: b.ts):
        o, h, l = _f(b.open), _f(b.high), _f(b.low)
        if side == "LONG":
            stop_hit, target_hit = l <= stop, h >= target
            stop_px = min(stop, o)
        else:
            stop_hit, target_hit = h >= stop, l <= target
            stop_px = max(stop, o)
        if stop_hit:
            return ("STOP", stop_px, b.ts)
        if target_hit:
            return ("TARGET", target, b.ts)
    return None


def pnl(side: str, entry, exit_px, qty) -> float:
    d = _f(exit_px) - _f(entry)
    return (d if side == "LONG" else -d) * _f(qty)


def _brokerage(notional: float, cfg: dict) -> float:
    return min(notional * cfg["brokerage_pct"] / 100.0, float(cfg["brokerage_cap"]))


def trade_costs(side: str, entry, exit_px, qty, cfg: dict) -> dict:
    """Itemised round-trip charges in rupees for the actual quantity.
    A long buys at entry and sells at exit; a short sells at entry and buys
    at exit -- STT falls on the sell leg, stamp duty on the buy leg."""
    q = _f(qty)
    entry_notional, exit_notional = _f(entry) * q, _f(exit_px) * q
    buy, sell = (entry_notional, exit_notional) if side == "LONG" else (exit_notional, entry_notional)
    brokerage = _brokerage(buy, cfg) + _brokerage(sell, cfg)
    exchange = (buy + sell) * cfg["exchange_txn_pct"] / 100.0
    sebi = (buy + sell) * cfg["sebi_pct"] / 100.0
    out = {
        "brokerage": brokerage,
        "stt": sell * cfg["stt_sell_pct"] / 100.0,
        "exchange": exchange,
        "sebi": sebi,
        "stamp": buy * cfg["stamp_duty_buy_pct"] / 100.0,
        "gst": cfg["gst_pct"] / 100.0 * (brokerage + exchange + sebi),
    }
    out["total"] = sum(out.values())
    return out
