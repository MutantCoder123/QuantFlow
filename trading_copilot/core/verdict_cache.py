"""Reuse the AI judge's verdict instead of asking again (agreed 2026-10-14, FUTURE_UPDATES 2.7 A).

The same stock and side within REUSE_S of a fresh judge call reuses that call's answer, unless:
  - the score moved MAX_SCORE_MOVE or more since the fresh call (the judge then agreed with
    itself only 57% of the time),
  - the regime changed, or
  - a paper position in that stock closed since (a new entry always gets a fresh verdict).
Only entry decisions are reused: while a position in the stock is open the judge is always asked
(it may be advising a close).
Over 3,597 recorded calls, 87% would be reused and they repeat the same answer 90% of the time
(GPT-OSS 95%); the flips were no better than the reused answers. Pure; one in-process store.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

REUSE_S = 30 * 60
MAX_SCORE_MOVE = 0.15


@dataclass
class Fresh:
    ts: float
    composite: float
    regime: str
    closed_n: int
    text: str


class VerdictCache:
    def __init__(self):
        self._d: dict[tuple[str, str], Fresh] = {}
        self.reused = 0
        self.fresh = 0
        self.models: dict[tuple[str, str], str] = {}     # the model that made each stored verdict

    def get(self, symbol: str, side: str, composite: float, regime: str, closed_n: int,
            now: float | None = None) -> str | None:
        f = self._d.get((symbol, side))
        now = time.time() if now is None else now
        if f is None or now - f.ts >= REUSE_S or abs(composite - f.composite) >= MAX_SCORE_MOVE \
                or regime != f.regime or closed_n != f.closed_n:
            return None
        self.reused += 1
        return f.text

    def put(self, symbol: str, side: str, composite: float, regime: str, closed_n: int, text: str,
            now: float | None = None, model: str | None = None) -> None:
        self.fresh += 1
        if model:
            self.models[(symbol, side)] = model
        self._d[(symbol, side)] = Fresh(time.time() if now is None else now, composite, regime, closed_n, text)


VERDICTS = VerdictCache()


def closed_count(symbol: str) -> int:
    """Paper positions in `symbol` closed so far (0 when the paper engine is not running)."""
    try:
        from paper import runtime
        b = runtime.broker() if hasattr(runtime, "broker") else getattr(runtime, "_broker", None)
        return sum(1 for t in (b.closed if b else []) if t.get("symbol") == symbol)
    except Exception:
        return 0


def has_open(symbol: str) -> bool:
    try:
        from paper import runtime
        b = runtime.broker()
        return bool(b and b.position_for(symbol))
    except Exception:
        return False
