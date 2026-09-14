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

    def load_day(self, date_str: str | None = None) -> pd.DataFrame:
        """Every row written for one IST session date.

        The log is write-only in the hot path; this is the read side the
        session review needs (Task 5.6). A missing partition is normal (no
        session, or nothing flushed yet) and yields an empty frame rather
        than an exception, matching ArmJournal.load_all.
        """
        day = date_str or datetime.datetime.now(_IST).strftime("%Y-%m-%d")
        part = self.data_dir / f"date={day}"
        if not part.is_dir():
            return pd.DataFrame()
        frames = []
        for f in sorted(part.glob("features_*.parquet")):
            try:
                frames.append(pd.read_parquet(f))
            except Exception:
                continue          # a partial/corrupt part must not hide the rest
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    def close(self) -> None:
        self.flush()


def staleness_incidents(df: pd.DataFrame) -> dict:
    """Count the stale-feed shield's rejections in one session's feature log.

    The shield (intraday_gatekeeper, A-12) rejects with math_rejection
    ``STALE_DATA_<age>s``, which _classify_decision carries into the
    ``decision`` column as ``GATED_STALE_DATA_<age>s``.

    Every field is None -- not 0 -- when there is nothing to count: no
    measurement is not a measurement of zero. A session with no feature log
    (none written, or nothing flushed yet) has an UNKNOWN number of stale-feed
    rejections, and reporting "0 stale-feed rejections" for it tells the
    operator the feed was clean when nobody looked. A present-but-clean
    session is different: 0 there is a real count, and stays 0.
    """
    if df is None or df.empty or "decision" not in df.columns:
        return {"incidents": None, "symbols": None,
                "max_stale_microstructure_s": None}

    hit = df[df["decision"].astype(str).str.contains("STALE_DATA", na=False)]

    max_stale = None
    if "stale_microstructure" in df.columns:
        col = pd.to_numeric(df["stale_microstructure"], errors="coerce").dropna()
        if not col.empty:
            max_stale = round(float(col.max()), 2)

    return {
        "incidents": int(len(hit)),
        "symbols": int(hit["symbol"].nunique()) if "symbol" in hit.columns else 0,
        "max_stale_microstructure_s": max_stale,
    }
