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
BAR_SECONDS = 300          # /api/bars serves five-minute bars, stamped at their start
EXIT_ACTIONS = {"CLOSE_EXISTING": "LLM_CLOSE", "REVERSE_POSITION": "LLM_REVERSE"}
# journal entry kind -> the decision it records
REVIEW_DECISION = {"CHECK": "hold", "TRAIL": "tighten", "RUN_ON": "run on",
                   "THESIS_AGAINST": "watch", "THESIS_OK": "hold", "THESIS_EXIT": "exit",
                   "TARGET_PULLED": "retarget", "FADE_TIGHTEN": "tighten"}
# a thesis run restarts if no fresh score arrived for this long (a feed gap
# must not be read as "still against")
THESIS_GAP_S = 180.0


def _hm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


class PaperBroker:
    def __init__(self, store: EventStore, settings, costs_cfg: dict,
                 clock=time.time, mirror=None, entry_cutoff: str = "13:45",
                 square_off: str = "15:20", config_version: int | None = None,
                 trail: list | tuple = (), reversal_cooldown_min: float = 0.0,
                 entry_start: str | None = None, run_on_trail_r: float | None = None,
                 thesis_against: float | None = None, thesis_minutes: float = 10.0,
                 review_every_min: float | None = None, reach_atr: float | None = None,
                 reach_floor_r: float = 1.0, reach_every_min: float = 5.0,
                 fade_frac: float | None = None, fade_minutes: float = 10.0, fade_min_open_r: float = 0.5):
        self.store = store
        # v5 (policy horizon.reach_target): a target more than reach_atr x ATR15
        # from the price is pulled in to that distance (never out, never inside
        # entry + reach_floor_r R), checked every reach_every_min.
        self.reach_atr = float(reach_atr) if reach_atr else None
        self.reach_floor_r = float(reach_floor_r)
        self.reach_every_s = float(reach_every_min or 5.0) * 60.0
        # v5 (policy horizon.memory): the score in the trade's favour has fallen
        # below fade_frac of its value at entry for fade_minutes -> the stop goes
        # to breakeven once fade_min_open_r is open.
        self.fade_frac = float(fade_frac) if fade_frac else None
        self.fade_s = float(fade_minutes or 0.0) * 60.0
        self.fade_min_open_r = float(fade_min_open_r)
        # v4 exits (policy horizon.run_on / thesis_exit), off when None.
        # run on: at the target keep the trade, stop run_on_trail_r behind the best.
        self.run_on_trail_r = float(run_on_trail_r) if run_on_trail_r else None
        # thesis: close once score x side <= -thesis_against for thesis_minutes.
        self.thesis_against = float(thesis_against) if thesis_against is not None else None
        self.thesis_s = float(thesis_minutes or 0.0) * 60.0
        # journal: a REVIEW event per open position this often (and at each decision)
        self.review_every_s = float(review_every_min) * 60.0 if review_every_min else None
        # no new position before this IST time (policy horizon.entry_start_ist)
        self.entry_start = _hm(entry_start) if entry_start else None
        # [(trigger_R, lock_R), ...]: once the best move reaches trigger_R the
        # stop moves to entry + lock_R (never back). policy horizon.trail.
        self.trail = sorted((float(a), float(b)) for a, b in (trail or ()))
        # after a losing close, no entry the other way on that symbol for this
        # many minutes. policy horizon.reversal_cooldown_min.
        self.reversal_cooldown_s = float(reversal_cooldown_min or 0.0) * 60.0
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
        """The deepest the day's realised P&L has been, as a positive loss.
        Latched: a later win does not bring it back. On 2026-09-30 the loss
        limit tripped at -25,149, a target at 12:55 lifted the day to
        -18,589, the gate reopened, and four more trades lost 4,602."""
        today = ist_date(self._now())
        running, worst = 0.0, 0.0
        for t in sorted((t for t in self.closed if ist_date(t["closed_ts"]) == today),
                        key=lambda t: t["closed_ts"]):
            running += t["net"]
            worst = min(worst, running)
        return -worst

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
        if self.entry_start and not self._past(self.entry_start):
            return self._reject(s, "BEFORE_ENTRY_START")
        if self.position_for(s["symbol"]):
            return self._reject(s, "ALREADY_OPEN")
        if s.get("manual_position"):
            return self._reject(s, "MANUAL_POSITION_HELD")
        if s.get("off_watchlist"):
            return self._reject(s, "NOT_ON_WATCHLIST")    # removed by the operator: no new trades
        if s.get("data_missing"):
            return self._reject(s, "DATA_INCOMPLETE")    # core.data_readiness: never trade on partial data
        if self._flipping(s["symbol"], side):
            return self._reject(s, "REVERSAL_COOLDOWN")

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
            book.add_open(p["symbol"], p["cluster"], p["risk_amount"],
                          value=float(p["qty"]) * float(p["entry_price"]))
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

    def _flipping(self, symbol: str, side: str) -> bool:
        """A losing close on `symbol` the other way, within the cooldown.
        2026-09-29: SCI and ADANIENT were each stopped out one way and taken
        the other way ~28 minutes later; both second trades stopped too."""
        if self.reversal_cooldown_s <= 0:
            return False
        since = self._now() - self.reversal_cooldown_s
        return any(t["symbol"] == symbol and t["side"] != side and t["net"] < 0
                   and t["closed_ts"] >= since for t in self.closed)

    # ------------------------------------------------------------------- exit
    def _stop_now(self, p: dict) -> float:
        """The stop in force: the trailed one once it has moved."""
        return float(p.get("trail_stop") or p["stop"])

    def _raise_trail(self, p: dict, best_r: float, at: float) -> None:
        """Move the stop up the trail for a best move of `best_r`. `at` is when
        the move was seen; bars that start before it cannot hit the new stop,
        since inside one bar the high and the low are not ordered. Past the
        target (run on), the stop also follows run_on_trail_r behind the best."""
        locks = [lk for trig, lk in self.trail if best_r >= trig]
        if p.get("past_target") and self.run_on_trail_r:
            locks.append(best_r - self.run_on_trail_r)
        lock = max(locks, default=None)
        if lock is None:
            return
        rps = abs(p["entry_price"] - p["stop"])
        level = p["entry_price"] + (lock if p["side"] == "LONG" else -lock) * rps
        better = level > self._stop_now(p) if p["side"] == "LONG" else level < self._stop_now(p)
        if better:
            p["trail_stop"], p["trail_ts"] = level, at
            step = int(lock * 2) / 2                      # journal each half-R step, not every tick
            if step > p.get("trail_step_r", float("-inf")):
                p["trail_step_r"] = step
                self._review(p, "TRAIL", "stop moved to breakeven" if abs(lock) < 1e-9
                             else f"stop raised to lock {lock:+.2f}R", at=at)

    # --------------------------------------------------------------- journal
    def _review(self, p: dict, kind: str, note: str, at: float | None = None) -> dict | None:
        """One journal entry for an open position: what is true now against
        what was true at entry, the decision and why. Written only when the
        policy turns the journal on (review_every_min)."""
        if self.review_every_s is None:
            return None
        ctx = p.get("context") or {}
        last = float(p.get("last") or p["entry_price"])
        p["last_review_ts"] = self._now()
        return self._emit({
            "type": "REVIEW", "pos_id": p["pos_id"], "symbol": p["symbol"], "kind": kind,
            "decision": REVIEW_DECISION.get(kind, "hold"), "note": note,
            "score": p.get("score_now"), "score_at_entry": ctx.get("composite"),
            "regime": p.get("regime_now"), "regime_at_entry": ctx.get("regime"),
            "r_now": self._r(p, last), "best_r": p.get("mfe_r", 0.0), "last": last,
            "stop": self._stop_now(p), "target": p["target"], "past_target": bool(p.get("past_target")),
            "seen_ts": at,
        })

    def _target_live(self, p: dict) -> float:
        """The target the touch checks use: none once the trade runs on past it
        (and only while the policy runs on: back on v3, the target counts again)."""
        if p.get("past_target") and self.run_on_trail_r:
            return float("inf") if p["side"] == "LONG" else float("-inf")
        return float(p["target"])

    def _reach_target(self, p: dict, at: float) -> dict | None:
        """The target was touched. Without run on: close there. With it: keep
        the trade, start the trail at target - run_on_trail_r, journal it."""
        if not self.run_on_trail_r:
            return self._close(p, float(p["target"]), "TARGET", market=False,
                               touch_check=p.pop("_touch", None))
        p.pop("_touch", None)
        p["past_target"], p["target_ts"] = True, at
        tgt_r = self._r(p, float(p["target"]))
        p["mfe_r"] = max(p.get("mfe_r", 0.0), tgt_r)
        self._review(p, "RUN_ON", f"target reached at {tgt_r:+.2f}R; running on, stop "
                                  f"{self.run_on_trail_r:g}R behind the best price", at=at)
        self._raise_trail(p, p["mfe_r"], at)
        return None

    def on_score(self, symbol: str, score, regime: str | None = None, fresh: bool = True,
                 ltp=None, atr15=None) -> dict | None:
        """The live composite for a symbol, every evaluation. Drives the
        thesis exit and the journal's periodic check. A score from stale data
        is ignored (it neither counts toward nor clears a run)."""
        p = self.position_for(symbol)
        if p is None:
            return None
        try:
            s = float(score)
        except (TypeError, ValueError):
            return None
        if not fresh:
            return None
        now = self._now()
        gap = now - p.get("score_ts", now)
        p["score_now"], p["score_ts"] = s, now
        if regime:
            p["regime_now"] = regime
        if self.thesis_against is not None and self.thesis_s > 0:
            side = 1.0 if p["side"] == "LONG" else -1.0
            if side * s <= -self.thesis_against:
                if p.get("against_since") is None or gap > THESIS_GAP_S:
                    p["against_since"] = now
                    self._review(p, "THESIS_AGAINST", f"the score now opposes the {p['side'].lower()}")
                elif now - p["against_since"] >= self.thesis_s:
                    px = float(ltp or p.get("last") or p["entry_price"])
                    self._review(p, "THESIS_EXIT", f"score has opposed the position for "
                                 f"{(now - p['against_since']) / 60:.0f} min; closing")
                    return self._close(p, px, "THESIS_EXIT", market=True)
            elif p.get("against_since") is not None:
                p["against_since"] = None
                self._review(p, "THESIS_OK", "the score no longer opposes the position")
        ev = self._retarget(p, atr15, now)
        if ev is not None:
            return ev
        self._fade(p, s, now, gap)
        if self.review_every_s and now - p.get("last_review_ts", p["ts"]) >= self.review_every_s:
            self._review(p, "CHECK", "periodic check")
        return None

    def _retarget(self, p: dict, atr15, now: float) -> dict | None:
        """Pull an out-of-reach target in to reach_atr x ATR15 from the price.
        2026-09-29..10-01: no target more than 4 ATR15 away was ever hit, and
        the trades behind them (BANKINDIA 7.5R, MCX, NBCC) gave back what a
        reachable target plus run on would have kept."""
        if not self.reach_atr or p.get("past_target"):
            return None
        if now - p.get("reach_ts", float("-inf")) < self.reach_every_s:
            return None
        try:
            atr = float(atr15)
        except (TypeError, ValueError):
            return None
        if not atr > 0:
            return None
        p["reach_ts"] = now
        last = float(p.get("last") or p["entry_price"])
        sgn = 1.0 if p["side"] == "LONG" else -1.0
        rps = abs(p["entry_price"] - p["stop"])
        cand = last + sgn * self.reach_atr * atr
        floor = p["entry_price"] + sgn * self.reach_floor_r * rps
        cand = max(cand, floor) if sgn > 0 else min(cand, floor)
        if sgn * (p["target"] - cand) <= 1e-9:
            return None                                      # already within reach: never pushed out
        old = p["target"]
        p["target"] = cand
        self._mirror_set(p)
        self._review(p, "TARGET_PULLED", f"target {old:.2f} was more than {self.reach_atr:g} ATR away; "
                                         f"now {cand:.2f} ({self._r(p, cand):+.2f}R)")
        if (last >= cand) if sgn > 0 else (last <= cand):
            return self._reach_target(p, now)
        return None

    def _fade(self, p: dict, s: float, now: float, gap: float) -> None:
        """Memory: the score's support for the trade against its value at
        entry. Faded below fade_frac for fade_s -> stop to breakeven once
        fade_min_open_r is open (NMDC 10-01: -5,281 -> -283 in replay)."""
        if not self.fade_frac or self.fade_s <= 0:
            return
        sgn = 1.0 if p["side"] == "LONG" else -1.0
        s0 = sgn * float((p.get("context") or {}).get("composite") or 0.0)
        if s0 <= 0:
            return
        if sgn * s >= self.fade_frac * s0:
            p.pop("fade_since", None)
            return
        if p.get("fade_since") is None or gap > THESIS_GAP_S:
            p["fade_since"] = now
            return
        if now - p["fade_since"] < self.fade_s:
            return
        last = float(p.get("last") or p["entry_price"])
        if self._r(p, last) < self.fade_min_open_r:
            return
        be = float(p["entry_price"])
        if (be > self._stop_now(p)) if sgn > 0 else (be < self._stop_now(p)):
            p["trail_stop"], p["trail_ts"] = be, now
            self._review(p, "FADE_TIGHTEN", f"the score ({s:+.2f}) has stayed below {self.fade_frac:.0%} of its "
                                            f"entry value for {self.fade_s / 60:.0f} min; stop to breakeven")

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
        self._raise_trail(p, p["mfe_r"], self._now())

    def _live_touch(self, p: dict, px: float) -> dict | None:
        """Stop or target against the live price. A stop fills at the stop or
        at the price if it is already past it (the worse of the two); a
        target fills at the target, as a resting limit order would."""
        if px <= 0:
            return None
        long = p["side"] == "LONG"
        stop = self._stop_now(p)
        if (px <= stop) if long else (px >= stop):
            fill = min(px, stop) if long else max(px, stop)
            return self._close(p, fill, self._stop_reason(p), market=False, touch_check="ltp")
        tgt = self._target_live(p)
        if (px >= tgt) if long else (px <= tgt):
            p["_touch"] = "ltp"
            return self._reach_target(p, self._now())
        return None

    def _stop_reason(self, p: dict) -> str:
        if p.get("past_target"):
            return "RUN_ON_STOP"                     # ran past the target, then trailed out
        return "TRAIL_STOP" if p.get("trail_stop") else "STOP"

    def check_touches(self, symbol: str, bars: list[Bar] | None, ltp=None) -> dict | None:
        """Stop/target against bars since the last check (intrabar touches
        between 10 s loop ticks count), bar by bar so a trail raised by one
        bar only applies to bars after it. Then the live price against a
        trailed stop. No bars: fall back to LTP, flagged."""
        p = self.position_for(symbol)
        if p is None:
            return None
        if bars:
            fresh = sorted((b for b in bars if b.ts >= p.get("last_bar_ts", p["ts"])), key=lambda b: b.ts)
            for b in fresh:
                in_force = self._stop_now(p) if b.ts >= p.get("trail_ts", 0.0) else p["stop"]
                hit = first_touch(p["side"], in_force, self._target_live(p), [b])
                if hit is not None:
                    reason, price, _ = hit
                    if reason == "TARGET":
                        p["_touch"] = "bars"
                        ev = self._reach_target(p, b.ts + BAR_SECONDS)
                        if ev is not None:
                            return ev
                    else:
                        if in_force != p["stop"]:
                            reason = self._stop_reason(p)
                        return self._close(p, price, reason, market=False, touch_check="bars")
                for px in (b.high, b.low):
                    r = self._r(p, float(px))
                    p["mae_r"], p["mfe_r"] = min(p["mae_r"], r), max(p["mfe_r"], r)
                self._raise_trail(p, p["mfe_r"], b.ts + BAR_SECONDS)
                p["last_bar_ts"] = b.ts
            # The live price, between bars. Bars come from the feed only once
            # each 5-minute bar completes, so on bars alone a crossed target
            # or stop was acted on 38-307 s late (2026-09-29..10-01: every
            # target exit landed on a 5-minute boundary).
            if ltp:
                return self._live_touch(p, float(ltp))
            return None
        if ltp:
            px = float(ltp)
            hit = first_touch(p["side"], self._stop_now(p), self._target_live(p),
                              [Bar(self._now(), px, px, px, px)])
            if hit is None:
                return None
            reason, price, _ = hit
            if reason == "TARGET":
                p["_touch"] = "ltp_only"
                return self._reach_target(p, self._now())
            return self._close(p, price, self._stop_reason(p), market=False, touch_check="ltp_only")
        return None

    # Path A rules paper does not act on. STOP_PROXIMITY closes anything
    # within a fixed 0.5 % of its stop -- a stand-in for "the stop is about to
    # be hit" when only an LTP is watched. Paper checks the real stop against
    # bar lows/highs, and the conviction geometry often sets stops tighter
    # than 0.5 %, so the rule closed those trades on the first tick (MCX,
    # 2026-09-29: stop 0.17 % away, closed after 6 s for -2,335). Ruling
    # 2026-09-29: paper ignores it; the gatekeeper is unchanged elsewhere.
    IGNORED_PATH_A_RULES = frozenset({"STOP_PROXIMITY"})

    def on_gatekeeper(self, symbol: str, res: dict, ltp) -> dict | None:
        """Act on Path A: 'Close' exits now, except the rules in
        IGNORED_PATH_A_RULES. (A Close is escalated to the LLM as well, but
        waiting on it near a stop would be reckless.)"""
        p = self.position_for(symbol)
        if p is None or str(res.get("Action", "")) != "Close":
            return None
        rule = res.get("Exit_Rule") or "UNSPECIFIED"
        if rule in self.IGNORED_PATH_A_RULES:
            return None
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
                            "ts": p.get("last_mark_ts"), "trail_stop": p.get("trail_stop"),
                            "trail_ts": p.get("trail_ts"), "past_target": p.get("past_target"),
                            "score": p.get("score_now")} for pid, p in self.open.items()},
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
                                            "stop", "trail_stop", "target", "risk_amount", "mae_r", "mfe_r", "ts",
                                            "last_mark_ts", "past_target", "score_now", "against_since")}
                     | {"unrealized": pnl(p["side"], p["entry_price"], p.get("last", p["entry_price"]), p["qty"]),
                        "r_now": self._r(p, p.get("last", p["entry_price"]))}
                     for p in self.open.values()],
            "unrealized": self.unrealized(), "realized_today": self.realized_today(),
            "open_risk": self.open_risk(),
            "daily_loss_limit": self.settings.effective()["capital"]
                                * self.settings.effective()["max_daily_loss_pct"] / 100.0,
        }
