"""Shadow-mode both-arm journalling (improved §4.5).

The system is premised on the LLM adding qualitative judgment over the
deterministic math layer -- but only CONFIRM/ADJUST verdicts were ever
journalled, so every ABORT and DEFER vanished and the premise could not be
tested even in principle.

An ArmRecord is written for EVERY escalation, carrying both arms:
  math_arm -- what the deterministic layer proposed
  llm_arm  -- what the LLM returned (including a veto)

Both arms are then labelled with the same outcome resolver, so the
math-only counterfactual is scored even when the LLM vetoed the trade.
That is what makes "does the LLM help?" answerable: if its ABORTs are no
better than random, the layer is expensive theatre; if they are precise,
that quantifies exactly what it is worth.
"""
from __future__ import annotations

import datetime
import json
import logging
from dataclasses import dataclass, field, asdict
from pathlib import Path
from zoneinfo import ZoneInfo

from journal.outcome_labeller import label_outcome

logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")

# An LLM arm only represents a trade actually taken when the verdict accepted
# the proposal. Anything else (ABORT/DEFER/UNKNOWN) is a veto: no position.
_TAKEN_VERDICTS = ("CONFIRM", "ADJUST")


@dataclass(frozen=True)
class ArmRecord:
    symbol: str
    ts: int
    config_version: int
    math_arm: dict            # {action, entry, stop, target}
    llm_arm: dict             # {verdict, action, entry, stop, target}
    escalated: bool = True
    extra: dict = field(default_factory=dict)

    def to_row(self) -> dict:
        row = asdict(self)
        row["arm_id"] = f"{self.symbol}-{self.ts}"
        # Outcomes are filled in later by the resolver.
        row.setdefault("math_outcome", None)
        row.setdefault("llm_outcome", None)
        return row


def arm_was_taken(llm_arm: dict) -> bool:
    """True when the LLM accepted the proposal (so a trade existed)."""
    return str((llm_arm or {}).get("verdict", "")).upper() in _TAKEN_VERDICTS


class ArmJournal:
    """Append-only JSONL, one file per IST session date."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _file(self, date_str: str | None = None) -> Path:
        day = date_str or datetime.datetime.now(_IST).strftime("%Y-%m-%d")
        return self.data_dir / f"arms_{day}.jsonl"

    def write(self, rec: ArmRecord | dict, date_str: str | None = None) -> None:
        row = rec.to_row() if isinstance(rec, ArmRecord) else rec
        try:
            with open(self._file(date_str), "a", encoding="utf-8") as f:
                f.write(json.dumps(row, default=str) + "\n")
        except Exception as e:
            logger.error(f"Failed to write arm record: {e}")

    def load_all(self, last_n_days: int = 30) -> list:
        """Newest version of every row across the last N session files.

        The journal is append-only, so a re-written (labelled) row appears
        after its original; keying on arm_id keeps the latest.
        """
        now = datetime.datetime.now(_IST)
        by_id: dict = {}
        for i in range(last_n_days):
            day = (now - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
            path = self._file(day)
            if not path.exists():
                continue
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    by_id[row.get("arm_id") or f"{row.get('symbol')}-{row.get('ts')}"] = row
        return list(by_id.values())


def label_arm_record(row: dict, bars, horizon_min: int = 90,
                     cost_pct: float = 0.06) -> dict:
    """Score BOTH arms of one record against the same bars.

    The math arm is always scored -- that is the counterfactual. The LLM arm
    is scored only when the LLM actually accepted the trade; a veto took no
    position, so its outcome stays None (and the math arm alone tells us
    whether the veto was right).
    """
    math_arm = row.get("math_arm") or {}
    llm_arm = row.get("llm_arm") or {}
    bias = str(math_arm.get("action", "LONG")).upper()
    if "SHORT" in bias:
        bias = "SHORT"
    elif "LONG" in bias:
        bias = "LONG"

    def _score(arm):
        try:
            entry = float(arm.get("entry") or 0)
            stop = float(arm.get("stop") or 0)
            target = float(arm.get("target") or 0)
        except (TypeError, ValueError):
            return None
        if entry <= 0 or stop <= 0 or target <= 0:
            return None
        return label_outcome(bars, int(row["ts"]), entry, stop, target,
                             bias, horizon_min=horizon_min, cost_pct=cost_pct)

    row["math_outcome"] = _score(math_arm)
    row["llm_outcome"] = _score(llm_arm) if arm_was_taken(llm_arm) else None
    return row


async def resolve_arms(journal: ArmJournal, fetch_bars, horizon_min: int = 90,
                       cost_pct: float = 0.06, days: int = 2) -> int:
    """Label any arm records whose horizon has elapsed but which carry no
    outcome yet. `fetch_bars(symbol)` is an async callable returning a bar
    DataFrame or None -- injected so this reuses the existing cross-process
    bridge without importing the ledger."""
    import time as _time
    now = _time.time()
    labelled = 0
    for row in journal.load_all(days):
        if row.get("math_outcome") is not None:
            continue
        try:
            if now - float(row["ts"]) < horizon_min * 60:
                continue          # horizon hasn't elapsed yet
        except (TypeError, ValueError, KeyError):
            continue
        bars = await fetch_bars(row.get("symbol"))
        if bars is None:
            continue
        journal.write(label_arm_record(row, bars, horizon_min, cost_pct))
        labelled += 1
    if labelled:
        logger.info(f"Labelled {labelled} arm record(s).")
    return labelled


def _arm_stats(outcomes: list) -> dict:
    resolved = [o for o in outcomes if o]
    n = len(resolved)
    if n == 0:
        return {"n": 0, "win_rate": None, "avg_r": None}
    wins = sum(1 for o in resolved if o.get("directional_correct"))
    avg_r = sum(float(o.get("r_multiple") or 0.0) for o in resolved) / n
    return {"n": n, "win_rate": round(wins / n * 100, 2), "avg_r": round(avg_r, 3)}


def summarise_arms(rows: list) -> dict:
    """Both-arm comparison plus LLM veto precision.

    veto precision = of the proposals the LLM vetoed, the share whose math
    arm would in fact have lost. High precision means the vetoes were saving
    money; ~50% means they were noise.
    """
    math_outcomes = [r.get("math_outcome") for r in rows]
    llm_outcomes = [r.get("llm_outcome") for r in rows]

    vetoed = [r for r in rows
              if not arm_was_taken(r.get("llm_arm") or {}) and r.get("math_outcome")]
    good_vetoes = sum(1 for r in vetoed
                      if not r["math_outcome"].get("directional_correct"))
    precision = round(good_vetoes / len(vetoed) * 100, 2) if vetoed else None

    return {
        "math_only": _arm_stats(math_outcomes),
        "math_plus_llm": _arm_stats(llm_outcomes),
        "llm_veto_precision": precision,
        "n_vetoed": len(vetoed),
        "n_escalations": len(rows),
    }
