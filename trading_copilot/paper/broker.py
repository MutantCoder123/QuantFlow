"""PaperBroker -- the autonomous paper account.

Opens a simulated position for every signal that passed all layers, sizes it
with core.risk against a real book of open paper risk and today's realised
paper P&L, mirrors it into ReasoningEngine.user_positions so the gatekeeper's
existing Path A manages it, and closes it on the first of: stop/target touch,
a Path A Close, an LLM close/reverse, or the 15:20 square-off.

Every decision is an event in the append-only store. A signal that cannot be
traded becomes a REJECT with a reason -- never silence. Nothing here places a
real order.

Failure policy: if an event cannot be persisted the broker marks itself
unhealthy (engine_ok=False) and stops opening positions, rather than trading
on state it could not record. Callers wrap every call; a broker error must
never break the decision loop.
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from core.risk import Portfolio, cluster_of, load_clusters, size
from core.types import Proposal
from paper.fills import Bar, entry_fill, first_touch, market_exit_fill, pnl, trade_costs
from paper.store import EventStore, rebuild, ist_date

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

ENTRY_ACTIONS = {"EXECUTE_LONG": "LONG", "EXECUTE_SHORT": "SHORT"}
EXIT_ACTIONS = {"CLOSE_EXISTING": "LLM_CLOSE", "REVERSE_POSITION": "LLM_REVERSE"}


def _hm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


class PaperBroker:
    def __init__(self, store: EventStore, settings, costs_cfg: dict,
                 clock=time.time, mirror=None, entry_cutoff: str = "13:45",
                 square_off: str = "15:20", config_version: int | None = None):
        self.store = store
        # the decision-policy version in force, stamped on every OPEN so
        # performance can be compared across tuning changes
        self.config_version = config_version
        self.settings = settings
        self.costs_cfg = costs_cfg
        # Same map core.risk.size() consults internally -- a different one
        # would book open risk under cluster names the budget check never sees.
        self.clusters = load_clusters()
        self.clock = clock
        self.mirror = mirror if mirror is not None else {}
        self.entry_cutoff = _hm(entry_cutoff)
        self.square_off_at = _hm(square_off)
        self.enabled = bool(settings.options().get("enabled", True))
        self.engine_ok = True
        self.last_error: str | None = None
        self.failed_at: float | None = None   # when persistence first failed
        self.error_count = 0
        self.open: dict = {}          # pos_id -> position
        self.closed: list = []        # closed trades (all days loaded)

    # ------------------------------------------------------------------ time
    def _now(self) -> float:
        return float(self.clock())

    def _ist(self, ts: float | None = None) -> datetime:
        return datetime.fromtimestamp(self._now() if ts is None else ts, IST)

    def _past(self, hm: tuple[int, int]) -> bool:
        t = self._ist()
        return (t.hour, t.minute) >= hm

    # ------------------------------------------------------------- persistence
    def _emit(self, event: dict) -> dict | None:
        event.setdefault("ts", self._now())
        try:
            return self.store.append(event)
        except Exception as e:
            if self.engine_ok:
                self.failed_at = self._now()
            self.engine_ok = False
            self.error_count += 1
            self.last_error = f"event log not writable: {e}"
            logger.error(f"paper: {self.last_error}")
            return None

    # ---------------------------------------------------------------- queries
    def position_for(self, symbol: str) -> dict | None:
        return next((p for p in self.open.values() if p["symbol"] == symbol), None)

    def realized_today(self) -> float:
        today = ist_date(self._now())
        return sum(t["net"] for t in self.closed if ist_date(t["closed_ts"]) == today)

    def realized_loss_today(self) -> float:
        return max(0.0, -self.realized_today())

    def open_risk(self) -> float:
        return sum(p["risk_amount"] for p in self.open.values())

    def unrealized(self) -> float:
        return sum(pnl(p["side"], p["entry_price"], p.get("last", p["entry_price"]), p["qty"])
                   for p in self.open.values())

    # ------------------------------------------------------------- lifecycle
    def pause(self):
        self.enabled = False

    def resume(self):
        self.enabled = True

    def recover(self) -> int:
        """Rebuild from the event log. A position opened on an earlier IST day
        is closed as RECOVERED_STALE at its last known price and flagged --
        never silently dropped. Returns the number of stale closes."""
        state = rebuild(self.store.load())
        self.open, self.closed = state.open, state.closed
        today, stale = ist_date(self._now()), 0
        for pid, pos in list(self.open.items()):
            if ist_date(pos["ts"]) < today:
                self._close(pos, float(pos.get("last") or pos["entry_price"]), "RECOVERED_STALE",
                            market=False, flags=["STALE_EXIT_PRICE"])
                stale += 1
            else:
                self._mirror_set(pos)
        return stale

    # ----------------------------------------------------------------- mirror
    def _mirror_set(self, p: dict):
        self.mirror[p["symbol"]] = {
            "source": "paper", "pos_id": p["pos_id"],
            "direction": "Long" if p["side"] == "LONG" else "Short",
            "entry_price": p["entry_price"], "stoploss": p["stop"], "target": p["target"],
            "qty": p["qty"], "entry_timestamp": p["ts"],
            "whale_cvd_at_entry": p.get("context", {}).get("whale_cvd", 0.0),
        }

    def _mirror_clear(self, p: dict):
        cur = self.mirror.get(p["symbol"])
        if isinstance(cur, dict) and cur.get("pos_id") == p["pos_id"]:
            del self.mirror[p["symbol"]]

    # ------------------------------------------------------------------ entry
    def _reject(self, s: dict, reason: str) -> dict | None:
        return self._emit({"type": "REJECT", "symbol": s.get("symbol"), "reason": reason,
                           "signal_id": s.get("signal_id"), "action": s.get("action"),
                           "context": self._context(s)})

    @staticmethod
    def _context(s: dict) -> dict:
        keys = ("verdict", "composite", "regime", "session_phase", "rationale",
                "attention_rank", "whale_cvd", "action")
        out = {}
        for k in keys:
            v = s.get(k)
            try:
                out[k] = float(v) if k in ("composite", "attention_rank", "whale_cvd") and v is not None else v
            except (TypeError, ValueError):
                out[k] = None
        return out

    def on_signal(self, s: dict) -> dict | None:
        """A CONFIRM/ADJUST ticket from the autonomous loop."""
        side = ENTRY_ACTIONS.get(str(s.get("action", "")).upper())
        if side is None:
            return self._reject(s, "NOT_AN_ENTRY")
        if not self.engine_ok:
            return None
        if not self.enabled:
            return self._reject(s, "ENGINE_PAUSED")
        if self._past(self.entry_cutoff):
            return self._reject(s, "AFTER_ENTRY_CUTOFF")
        if self.position_for(s["symbol"]):
            return self._reject(s, "ALREADY_OPEN")
        if s.get("manual_position"):
            return self._reject(s, "MANUAL_POSITION_HELD")

        opts = self.settings.options()
        try:
            ltp = float(s.get("ltp") or 0.0)
            stop, target = float(s.get("stop") or 0.0), float(s.get("target") or 0.0)
            age = float(s.get("data_age_s") or 0.0)
            adv = float(s.get("adv_shares") or 0.0)
        except (TypeError, ValueError):
            return self._reject(s, "NO_GEOMETRY")
        if stop <= 0 or target <= 0:
            return self._reject(s, "NO_GEOMETRY")
        if ltp <= 0 or age > opts["stale_price_seconds"]:
            return self._reject(s, "NO_FRESH_PRICE")

        eff = self.settings.effective()
        fill = entry_fill(side, ltp, eff["slippage_pct"])
        ok_geo = stop < fill < target if side == "LONG" else target < fill < stop
        if not ok_geo:
            return self._reject(s, "PRICE_BEYOND_GEOMETRY")
        if adv <= 0:
            return self._reject(s, "NO_ADV")

        book = Portfolio(realized_loss_today=self.realized_loss_today())
        for p in self.open.values():
            book.add_open(p["symbol"], p["cluster"], p["risk_amount"])
        prop = Proposal(symbol=s["symbol"], bias=side, entry=fill, stop=stop, target=target,
                        composite=float(s.get("composite") or 0.0),
                        regime=str(s.get("regime") or "UNKNOWN"))
        sized = size(prop, book, self.settings.risk_limits(), adv)
        if getattr(sized, "reason", None):
            return self._reject(s, sized.reason)

        cluster = cluster_of(s["symbol"], self.clusters)
        ev = self._emit({
            "type": "OPEN", "pos_id": uuid.uuid4().hex[:12], "symbol": s["symbol"],
            "token": s.get("token"), "side": side, "qty": int(sized.qty),
            "entry_price": fill, "ltp_at_entry": ltp, "stop": stop, "target": target,
            "risk_amount": float(sized.risk_amount), "cluster": cluster,
            "signal_id": s.get("signal_id"), "config_version": self.config_version,
            "settings": eff, "context": self._context(s),
        })
        if ev is None:
            return None
        pos = dict(ev, mae_r=0.0, mfe_r=0.0, last=fill, last_bar_ts=ev["ts"])
        self.open[pos["pos_id"]] = pos
        self._mirror_set(pos)
        return ev

    # ------------------------------------------------------------------- exit
    def _r(self, p: dict, price: float) -> float:
        rps = abs(p["entry_price"] - p["stop"])
        if rps <= 0:
            return 0.0
        d = price - p["entry_price"]
        return (d if p["side"] == "LONG" else -d) / rps

    def _close(self, p: dict, price: float, reason: str, market: bool,
               touch_check: str | None = None, flags: list | None = None) -> dict | None:
        eff = p.get("settings") or self.settings.effective()
        fill = market_exit_fill(p["side"], price, eff.get("slippage_pct", 0.0)) if market else float(price)
        gross = pnl(p["side"], p["entry_price"], fill, p["qty"])
        costs = trade_costs(p["side"], p["entry_price"], fill, p["qty"], self.costs_cfg)
        net = gross - costs["total"]
        risk = p["risk_amount"] or 0.0
        exit_r = self._r(p, fill)
        ev = self._emit({
            "type": "CLOSE", "pos_id": p["pos_id"], "symbol": p["symbol"],
            "exit_price": fill, "exit_ref_price": float(price), "reason": reason,
            "touch_check": touch_check, "flags": flags or [],
            "gross": gross, "costs": costs, "net": net,
            "r_gross": gross / risk if risk else None, "r_net": net / risk if risk else None,
            "mae_r": min(p.get("mae_r", 0.0), exit_r), "mfe_r": max(p.get("mfe_r", 0.0), exit_r),
            "hold_min": (self._now() - p["ts"]) / 60.0,
        })
        if ev is None:
            return None
        self.open.pop(p["pos_id"], None)
        trade = {k: v for k, v in p.items() if k not in ("type", "id", "ts", "last", "last_bar_ts")}
        trade.update({k: v for k, v in ev.items() if k not in ("type", "id")})
        trade["opened_ts"], trade["closed_ts"] = p["ts"], ev["ts"]
        self.closed.append(trade)
        self._mirror_clear(p)
        return ev

    def mark(self, symbol: str, ltp) -> None:
        p = self.position_for(symbol)
        if p is None:
            return
        try:
            px = float(ltp)
        except (TypeError, ValueError):
            return
        if px <= 0:
            return
        p["last"], p["last_mark_ts"] = px, self._now()
        r = self._r(p, px)
        p["mae_r"], p["mfe_r"] = min(p.get("mae_r", 0.0), r), max(p.get("mfe_r", 0.0), r)

    def check_touches(self, symbol: str, bars: list[Bar] | None, ltp=None) -> dict | None:
        """Stop/target against bars since the last check (intrabar touches
        between 10 s loop ticks count). No bars: fall back to LTP, flagged."""
        p = self.position_for(symbol)
        if p is None:
            return None
        if bars:
            fresh = [b for b in bars if b.ts >= p.get("last_bar_ts", p["ts"])]
            for b in fresh:
                for px in (b.high, b.low):
                    r = self._r(p, float(px))
                    p["mae_r"], p["mfe_r"] = min(p["mae_r"], r), max(p["mfe_r"], r)
            hit = first_touch(p["side"], p["stop"], p["target"], fresh)
            if fresh:
                p["last_bar_ts"] = max(b.ts for b in fresh)
            check = "bars"
        elif ltp:
            px = float(ltp)
            hit = first_touch(p["side"], p["stop"], p["target"], [Bar(self._now(), px, px, px, px)])
            check = "ltp_only"
        else:
            return None
        if hit is None:
            return None
        reason, price, _ = hit
        return self._close(p, price, reason, market=False, touch_check=check)

    def on_gatekeeper(self, symbol: str, res: dict, ltp) -> dict | None:
        """Act on Path A: 'Close' exits now. (A Close is escalated to the LLM
        as well, but waiting on it near a stop would be reckless.)"""
        p = self.position_for(symbol)
        if p is None or str(res.get("Action", "")) != "Close":
            return None
        rule = res.get("Exit_Rule") or "UNSPECIFIED"
        reason = "SQUARE_OFF" if rule == "SQUARE_OFF" else f"GATEKEEPER_{rule}"
        return self._close(p, float(ltp or p.get("last") or p["entry_price"]), reason, market=True)

    def on_llm_directive(self, symbol: str, action: str, ltp) -> dict | None:
        p = self.position_for(symbol)
        reason = EXIT_ACTIONS.get(str(action).upper())
        if p is None or reason is None:
            return None
        return self._close(p, float(ltp or p.get("last") or p["entry_price"]), reason, market=True)

    def square_off_all(self, ltp_of) -> list:
        """At/after the square-off time, close everything at market.
        `ltp_of(symbol)` -> latest price or None (then last mark, flagged)."""
        if not self._past(self.square_off_at):
            return []
        out = []
        for p in list(self.open.values()):
            px = ltp_of(p["symbol"])
            flags = [] if px else ["STALE_EXIT_PRICE"]
            ev = self._close(p, float(px or p.get("last") or p["entry_price"]), "SQUARE_OFF",
                             market=True, flags=flags)
            if ev:
                out.append(ev)
        return out

    # --------------------------------------------------------------- equity
    def equity_snapshot(self) -> dict | None:
        cap = self.settings.effective()["capital"]
        realized_total = sum(t["net"] for t in self.closed)
        unreal = self.unrealized()
        return self._emit({
            "type": "EQUITY", "capital": cap, "realized_total": realized_total,
            "realized_today": self.realized_today(), "unrealized": unreal,
            "equity": cap + realized_total + unreal, "open_risk": self.open_risk(),
            "open_count": len(self.open),
            "marks": {pid: {"last": p.get("last"), "mae_r": p.get("mae_r"), "mfe_r": p.get("mfe_r"),
                            "ts": p.get("last_mark_ts")} for pid, p in self.open.items()},
        })

    # --------------------------------------------------------------- settings
    def update_settings(self, changes: dict) -> tuple[bool, dict]:
        old = self.settings.effective()
        ok, errors = self.settings.update(changes)
        if ok:
            new = self.settings.effective()
            diff_old = {k: old[k] for k in changes if old.get(k) != new.get(k)}
            diff_new = {k: new[k] for k in changes if old.get(k) != new.get(k)}
            if diff_new:
                self._emit({"type": "SETTINGS", "old": diff_old, "new": diff_new})
        return ok, errors

    # ---------------------------------------------------------------- summary
    def summary(self) -> dict:
        """Cheap live block for the /ws payload."""
        return {
            "engine_ok": self.engine_ok, "enabled": self.enabled, "last_error": self.last_error,
            "failed_at": self.failed_at,
            "error_count": self.error_count, "as_of": self._now(),
            "open": [{k: p.get(k) for k in ("pos_id", "symbol", "side", "qty", "entry_price", "last",
                                            "stop", "target", "risk_amount", "mae_r", "mfe_r", "ts",
                                            "last_mark_ts")}
                     | {"unrealized": pnl(p["side"], p["entry_price"], p.get("last", p["entry_price"]), p["qty"]),
                        "r_now": self._r(p, p.get("last", p["entry_price"]))}
                     for p in self.open.values()],
            "unrealized": self.unrealized(), "realized_today": self.realized_today(),
            "open_risk": self.open_risk(),
            "daily_loss_limit": self.settings.effective()["capital"]
                                * self.settings.effective()["max_daily_loss_pct"] / 100.0,
        }
