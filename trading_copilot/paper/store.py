"""Append-only paper event log, and the pure replay that rebuilds state.

One JSONL file per IST trading day: data/paper/events_YYYY-MM-DD.jsonl.
Event types: OPEN, CLOSE, REJECT, SETTINGS, EQUITY (per-minute snapshot,
carrying each open position's latest mark so MAE/MFE survive a restart).
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def ist_date(ts: float) -> str:
    return datetime.fromtimestamp(ts, IST).strftime("%Y-%m-%d")


class EventStore:
    def __init__(self, directory: Path | None = None):
        if directory is None:
            from paths import DATA_DIR
            directory = DATA_DIR / "paper"
        self.dir = Path(directory)
        self.skipped_lines = 0
        self._lock = threading.Lock()

    def append(self, event: dict) -> dict:
        """Persist one event; raises on I/O failure (the broker turns that
        into engine_ok=False rather than trading on unpersisted state)."""
        e = dict(event)
        e.setdefault("id", uuid.uuid4().hex)
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / f"events_{ist_date(e['ts'])}.jsonl"
        line = json.dumps(e, separators=(",", ":"), default=float)
        with self._lock, open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
        return e

    def load(self) -> list[dict]:
        out = []
        self.skipped_lines = 0
        for path in sorted(self.dir.glob("events_*.jsonl")):
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        self.skipped_lines += 1
        if self.skipped_lines:
            logger.warning(f"paper store: skipped {self.skipped_lines} unreadable line(s)")
        out.sort(key=lambda e: e.get("ts", 0))
        return out


@dataclass
class State:
    open: dict = field(default_factory=dict)             # pos_id -> position
    closed: list = field(default_factory=list)           # trades, in close order
    rejections: list = field(default_factory=list)
    settings_changes: list = field(default_factory=list)
    equity: list = field(default_factory=list)           # EQUITY snapshots


def rebuild(events: list[dict]) -> State:
    """Replay events (ts order) into state. Idempotent under duplicates."""
    s, seen = State(), set()
    for e in sorted(events, key=lambda e: e.get("ts", 0)):
        eid = e.get("id")
        if eid is not None:
            if eid in seen:
                continue
            seen.add(eid)
        t = e.get("type")
        if t == "OPEN":
            pos = dict(e)
            pos.setdefault("mae_r", 0.0)
            pos.setdefault("mfe_r", 0.0)
            pos.setdefault("last", e.get("entry_price"))
            s.open[e["pos_id"]] = pos
        elif t == "CLOSE":
            pos = s.open.pop(e["pos_id"], None)
            if pos is None:
                continue                          # close of an unknown/already-closed position
            trade = {k: v for k, v in pos.items() if k not in ("type", "id", "ts", "last")}
            trade.update({k: v for k, v in e.items() if k not in ("type", "id")})
            trade["opened_ts"], trade["closed_ts"] = pos["ts"], e["ts"]
            s.closed.append(trade)
        elif t == "REJECT":
            s.rejections.append(e)
        elif t == "SETTINGS":
            s.settings_changes.append(e)
        elif t == "EQUITY":
            s.equity.append(e)
            for pid, m in (e.get("marks") or {}).items():
                if pid in s.open:
                    s.open[pid].update({k: m[k] for k in ("last", "mae_r", "mfe_r") if k in m})
    return s
