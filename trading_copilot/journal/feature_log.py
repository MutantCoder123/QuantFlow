"""Append-only feature-vector capture — the evidence layer.

Logs EVERY symbol on EVERY evaluation, including rejections, so that:
  - thresholds can be fitted rather than guessed,
  - feature importance can be measured,
  - the counterfactual (what we did NOT take) exists.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

_IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class FeatureRecord:
    ts: int
    symbol: str
    config_version: int
    features: dict[str, float]
    staleness: dict[str, float]
    regime: str
    session_phase: str
    composite: float | None
    decision: str                 # PROPOSED | REJECTED_<reason> | GATED_<reason>
    llm_verdict: str | None = None
    extra: dict[str, float] = field(default_factory=dict)

    def to_row(self) -> dict:
        row = {"ts": self.ts, "symbol": self.symbol,
               "config_version": self.config_version, "regime": self.regime,
               "session_phase": self.session_phase, "composite": self.composite,
               "decision": self.decision, "llm_verdict": self.llm_verdict}
        row.update({f"f_{k}": v for k, v in self.features.items()})
        row.update({f"stale_{k}": v for k, v in self.staleness.items()})
        row.update({f"x_{k}": v for k, v in self.extra.items()})
        return row


class FeatureLog:
    def __init__(self, data_dir: Path, flush_n: int = 2_000):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._flush_n = flush_n
        self._buf: list[dict] = []
        self._seq = 0

    def write(self, rec: FeatureRecord) -> None:
        self._buf.append(rec.to_row())
        if len(self._buf) >= self._flush_n:
            self.flush()

    def flush(self) -> Path | None:
        if not self._buf:
            return None
        df = pd.DataFrame(self._buf)      # union of keys; missing -> NaN
        self._buf.clear()
        self._seq += 1
        day = datetime.datetime.now(_IST).strftime("%Y-%m-%d")
        part = self.data_dir / f"date={day}"
        part.mkdir(parents=True, exist_ok=True)
        out = part / f"features_{self._seq:06d}.parquet"
        df.to_parquet(out, engine="pyarrow", compression="zstd", index=False)
        return out

    def close(self) -> None:
        self.flush()
