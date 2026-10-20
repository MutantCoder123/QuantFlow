"""The pre-open auction book (policy v8): paper only, its own event store (data/paper_auction).

One day:
  08:45-09:07:30  PLAN: score every stock from yesterday's close, long the top n, short the
                  bottom n (liquid only). The orders would go into NSE's pre-open call auction,
                  which closes at ~09:08, so a plan made later is not traded that day.
  09:16           OPEN: each position fills at the official open (the auction price = the open
                  of the 09:15 minute). No spread at entry: an auction order pays the auction price.
  every 5 min     marks; the book is closed early if it loses kill_loss_pct of capital.
  15:10           CLOSE: market exit at the 15:09 minute's close, less slippage; real charges.
No stop per position: the research trade had none (EDGE_RESEARCH.md).
Positions are sized by notional: capital x gross_x / (2 n) each.
"""
from __future__ import annotations

import math
import time
import uuid
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
PLAN_FROM, PLAN_BY = dtime(8, 45), dtime(9, 7, 30)
FILL_FROM, FILL_GIVE_UP = dtime(9, 16, 15), dtime(9, 30)
EXIT_AT, EXIT_MINUTE = dtime(15, 10, 20), "15:09"
DEFAULTS = {"enabled": False, "n_per_side": 5, "gross_x": 5.0, "min_adv_crore": 50.0, "kill_loss_pct": 6.0}


def config() -> dict:
    try:
        from core.policy_config import load_policy
        return {**DEFAULTS, **((load_policy().books or {}).get("auction") or {})}
    except Exception:
        return dict(DEFAULTS)


def ist_day(ts: float) -> str:
    return datetime.fromtimestamp(ts, IST).strftime("%Y-%m-%d")


class AuctionBook:
    def __init__(self, store, capital: float, slippage_pct: float, costs_cfg: dict, cfg: dict, clock=time.time):
        self.store, self.capital, self.slip, self.costs_cfg, self.cfg, self.clock = store, capital, slippage_pct, costs_cfg, cfg, clock
        self.events = [e for e in store.load()]

    # ------------------------------------------------------------------ state
    def today(self, day: str) -> list[dict]:
        return [e for e in self.events if e.get("day") == day]

    def plan_for(self, day: str) -> dict | None:
        return next((e for e in self.today(day) if e["type"] == "PLAN"), None)

    def skipped(self, day: str) -> dict | None:
        """The day's SKIP, unless a PLAN was made after all (a late simulated day, make_plan(late_ok))."""
        if self.plan_for(day):
            return None
        return next((e for e in self.today(day) if e["type"] == "SKIP" and not e.get("symbol")), None)

    def open_positions(self, day: str) -> list[dict]:
        closed = {e["pos_id"] for e in self.today(day) if e["type"] == "CLOSE"}
        return [e for e in self.today(day) if e["type"] == "OPEN" and e["pos_id"] not in closed]

    def filled(self, day: str) -> bool:
        return any(e["type"] in ("OPEN", "FILL_DONE") for e in self.today(day))

    def _emit(self, e: dict) -> dict:
        e = {"ts": self.clock(), **e}
        e.setdefault("id", uuid.uuid4().hex)
        self.store.append(e)
        self.events.append(e)
        return e

    # ------------------------------------------------------------------ steps
    def make_plan(self, day: str, now: datetime, picks: dict, scores: dict, asof: str,
                  late_ok: bool = False) -> dict:
        """late_ok (paper only, user's call): a plan made after the order window, from data through
        yesterday only, filled at the official open as if it had been in the auction. Tagged."""
        if now.time() > PLAN_BY and not late_ok:
            return self.skip(day, f"plan at {now:%H:%M:%S}, after the pre-open order window (09:07:30)")
        n = max(len(picks["long"]), len(picks["short"]), 1)
        notional = self.capital * float(self.cfg["gross_x"]) / (2 * n)
        return self._emit({"type": "PLAN", "day": day, "created_ist": now.strftime("%H:%M:%S"),
                           "long": picks["long"], "short": picks["short"], "notional": notional,
                           "scores": {s: round(float(scores[s]), 6) for s in picks["long"] + picks["short"]},
                           "data_through": asof, "cfg": dict(self.cfg),
                           **({"late_simulated": True} if late_ok and now.time() > PLAN_BY else {})})

    def skip(self, day: str, reason: str, symbol: str | None = None) -> dict:
        return self._emit({"type": "SKIP", "day": day, "reason": reason, **({"symbol": symbol} if symbol else {})})

    def fill(self, day: str, opens: dict) -> list[dict]:
        """OPEN each planned stock at its official open; SKIP the ones without one."""
        plan = self.plan_for(day)
        out = []
        for side, syms in (("LONG", plan["long"]), ("SHORT", plan["short"])):
            for s in syms:
                px = opens.get(s)
                if not px or not math.isfinite(px) or px <= 0:
                    self.skip(day, "NO_OPEN_PRICE", symbol=s)
                    continue
                qty = int(plan["notional"] // px)
                if qty < 1:
                    self.skip(day, "NOTIONAL_BELOW_ONE_SHARE", symbol=s)
                    continue
                out.append(self._emit({"type": "OPEN", "day": day, "pos_id": uuid.uuid4().hex[:12], "symbol": s,
                                       "side": side, "qty": qty, "entry_price": float(px), "fill": "AUCTION_OPEN",
                                       "score": plan["scores"].get(s)}))
        self._emit({"type": "FILL_DONE", "day": day, "n": len(out)})
        return out

    def unrealized(self, day: str, prices: dict) -> float:
        tot = 0.0
        for p in self.open_positions(day):
            px = prices.get(p["symbol"])
            if px:
                d = float(px) - p["entry_price"]
                tot += (d if p["side"] == "LONG" else -d) * p["qty"]
        return tot

    def kill_hit(self, day: str, prices: dict) -> bool:
        lim = float(self.cfg.get("kill_loss_pct") or 0.0)
        return lim > 0 and self.unrealized(day, prices) <= -lim / 100.0 * self.capital

    def close_all(self, day: str, prices: dict, reason: str) -> list[dict]:
        from paper.fills import market_exit_fill, pnl, trade_costs
        out = []
        for p in self.open_positions(day):
            px = prices.get(p["symbol"])
            if not px:
                continue
            fill = market_exit_fill(p["side"], px, self.slip)
            gross = pnl(p["side"], p["entry_price"], fill, p["qty"])
            costs = trade_costs(p["side"], p["entry_price"], fill, p["qty"], self.costs_cfg)
            notional = p["entry_price"] * p["qty"]
            out.append(self._emit({"type": "CLOSE", "day": day, "pos_id": p["pos_id"], "symbol": p["symbol"],
                                   "side": p["side"], "exit_ref_price": float(px), "exit_price": fill, "reason": reason,
                                   "gross": gross, "costs": costs, "net": gross - costs["total"],
                                   "bps_gross": gross / notional * 1e4, "bps_net": (gross - costs["total"]) / notional * 1e4}))
        return out

    def summary(self, day: str, prices: dict | None = None) -> dict:
        ev = self.today(day)
        closes = [e for e in ev if e["type"] == "CLOSE"]
        opens = {e["pos_id"]: e for e in ev if e["type"] == "OPEN"}
        rows = []
        for pid, o in opens.items():
            c = next((x for x in closes if x["pos_id"] == pid), None)
            last = (c or {}).get("exit_ref_price") or (prices or {}).get(o["symbol"])
            d = (float(last) - o["entry_price"]) if last else 0.0
            rows.append({"symbol": o["symbol"], "side": o["side"], "qty": o["qty"], "entry": o["entry_price"],
                         "last": last, "pnl": (c or {}).get("net", (d if o["side"] == "LONG" else -d) * o["qty"]),
                         "closed": bool(c), "reason": (c or {}).get("reason")})
        plan = self.plan_for(day)
        status = ("skipped" if self.skipped(day) else "closed" if opens and len(closes) >= len(opens)
                  else "open" if opens else "planned" if plan else "idle")
        return {"day": day, "status": status, "skip": (self.skipped(day) or {}).get("reason"),
                "plan": {k: plan[k] for k in ("long", "short", "notional", "data_through", "created_ist")} if plan else None,
                "positions": rows, "realized": sum(c["net"] for c in closes),
                "unrealized": sum(r["pnl"] for r in rows if not r["closed"]), "ts": self.clock()}
