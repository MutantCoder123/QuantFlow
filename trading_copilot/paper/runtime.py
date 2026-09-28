"""The one PaperBroker for the API/main process, and the guarded entry points
ReasoningEngine calls.

Every function here swallows and records its own errors: the paper engine
is an observer of the decision loop and must never be able to break it.
"""
from __future__ import annotations

import asyncio
import logging

from paper.broker import PaperBroker
from paper.fills import Bar
from paper.settings import PaperSettings
from paper.store import EventStore

logger = logging.getLogger(__name__)

_broker: PaperBroker | None = None


def init(mirror: dict) -> PaperBroker | None:
    """Build the broker over the real log/config, recover state, and mirror
    open positions into `mirror` (ReasoningEngine.user_positions)."""
    global _broker
    if _broker is not None:
        return _broker
    try:
        from core.costs import load_costs
        from core.policy_config import load_policy
        policy = load_policy()
        horizon = policy.horizon
        _broker = PaperBroker(store=EventStore(), settings=PaperSettings(), costs_cfg=load_costs(),
                              mirror=mirror,
                              entry_cutoff=horizon.get("entry_cutoff_ist", "13:45"),
                              square_off=horizon.get("square_off_ist", "15:20"),
                              config_version=policy.version)
        stale = _broker.recover()
        logger.info(f"Paper engine ready: {len(_broker.open)} open, {len(_broker.closed)} closed"
                    + (f", {stale} stale position(s) closed" if stale else ""))
    except Exception as e:
        logger.error(f"Paper engine failed to start: {e}")
        _broker = None
    return _broker


def broker() -> PaperBroker | None:
    return _broker


def _guard(fn, *args, **kwargs):
    b = _broker
    if b is None:
        return None
    try:
        return fn(b, *args, **kwargs)
    except Exception as e:
        b.error_count += 1
        b.last_error = f"{type(e).__name__}: {e}"
        logger.error(f"paper: {fn.__name__} failed: {e}")
        return None


def autonomous() -> bool:
    """Paper mode escalates every authorised signal to the judge, regardless
    of the per-symbol AI toggle."""
    try:
        return bool(_broker is not None and _broker.enabled
                    and _broker.settings.options().get("autonomous", True))
    except Exception:
        return False


def judge_model(default: str = "gemini-2.5-flash") -> str:
    try:
        return PaperSettings().options().get("judge_model") or default
    except Exception:
        return default


def realized_loss_today() -> float:
    return _guard(lambda b: b.realized_loss_today()) or 0.0


def on_ticket(symbol: str, ticket: dict, risk_params: dict, math_setup: dict,
              regime: dict, raw: dict, signal_id: str | None, manual_position: bool):
    """Called where a CONFIRM/ADJUST ticket is recorded."""
    action = str(ticket.get("action_directive", "")).upper()

    def _do(b: PaperBroker):
        ltp = raw.get("ltp")
        if action in ("CLOSE_EXISTING", "REVERSE_POSITION"):
            return b.on_llm_directive(symbol, action, ltp)
        return b.on_signal({
            "symbol": symbol, "token": raw.get("token"), "action": action,
            "verdict": ticket.get("verdict"), "ltp": ltp,
            "stop": risk_params.get("final_stop"), "target": risk_params.get("final_target"),
            "data_age_s": raw.get("data_age_s", 0.0), "adv_shares": raw.get("adv_shares"),
            "whale_cvd": raw.get("whale_cvd_ema_1h"),
            "composite": math_setup.get("composite_score"),
            "regime": regime.get("current_regime"), "session_phase": regime.get("session_phase"),
            "signal_id": signal_id, "rationale": ticket.get("institutional_rationale"),
            "manual_position": manual_position,
        })
    return _guard(_do)


def on_gatekeeper(symbol: str, res: dict, ltp):
    """Every loop tick, for every symbol: mark, then act on a Path A Close."""
    def _do(b: PaperBroker):
        if b.position_for(symbol) is None:
            return None
        b.mark(symbol, ltp)
        return b.on_gatekeeper(symbol, res, ltp)
    return _guard(_do)


async def _fetch_bars(token: str, n: int = 30) -> list[Bar] | None:
    import aiohttp
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("http://127.0.0.1:8001/api/bars", params={"token": token, "n": n},
                                   timeout=aiohttp.ClientTimeout(total=3)) as resp:
                data = await resp.json()
    except Exception:
        return None
    out = []
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ist = ZoneInfo("Asia/Kolkata")
    for r in data.get("bars") or []:
        try:
            ts = datetime.fromisoformat(str(r["timestamp"]))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=ist)
            out.append(Bar(ts=ts.timestamp(), open=float(r["open"]), high=float(r["high"]),
                           low=float(r["low"]), close=float(r["close"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out or None


async def tick(ltp_of, fetch_bars=None, last_snap: float = 0.0) -> float:
    """One pass: mark and touch-check every open position (bars since the
    last check; LTP-only fallback, flagged), square off at/after the
    square-off time, and snapshot equity on its cadence. `ltp_of(symbol)` ->
    (ltp, token) or (None, None). Returns the time of the last snapshot."""
    b = _broker
    if b is None:
        return last_snap
    fetch_bars = fetch_bars or _fetch_bars
    try:
        for p in list(b.open.values()):
            ltp, token = ltp_of(p["symbol"])
            if ltp:
                b.mark(p["symbol"], ltp)
            tok = token or p.get("token")
            bars = await fetch_bars(tok) if tok else None
            b.check_touches(p["symbol"], bars, ltp=ltp)
        b.square_off_all(lambda s: ltp_of(s)[0])
        now = b._now()
        every = b.settings.options().get("equity_snapshot_seconds", 60)
        if now - last_snap >= every and (b.open or b.closed):
            b.equity_snapshot()
            last_snap = now
    except Exception as e:
        b.error_count += 1
        b.last_error = f"{type(e).__name__}: {e}"
        logger.error(f"paper loop: {e}")
    return last_snap


async def run(ltp_of, interval_s: float = 10.0):
    """Background task: tick() every `interval_s` seconds."""
    last_snap = 0.0
    while True:
        last_snap = await tick(ltp_of, last_snap=last_snap)
        await asyncio.sleep(interval_s)
