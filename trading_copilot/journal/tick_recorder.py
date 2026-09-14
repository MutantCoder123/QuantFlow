"""Append-only tick capture.

Microstructure features (OBI, CVD, whale CVD, session VWAP, intraday POC) are
40-50% of the composite score and are derived from data that was previously
consumed and discarded. Without this file they can never be validated.

Volume: ~2.4M rows/day for 27 symbols; ~40-60 MB/day compressed.
"""
from __future__ import annotations

import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

_COLUMNS = ["token", "ts_ms", "ltp", "vtt", "oi", "bid1", "ask1", "bid_qty", "ask_qty"]
_IST = ZoneInfo("Asia/Kolkata")


class TickRecorder:
    def __init__(self, data_dir: Path, flush_n: int = 20_000):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._flush_n = flush_n
        self._buf: list[tuple] = []
        self._seq = 0

    def record(self, token: str, ts_ms: int, ltp: float, vtt: float, oi: float,
               bid1: float, ask1: float, bid_qty: int, ask_qty: int) -> None:
        self._buf.append((token, int(ts_ms), float(ltp), float(vtt), float(oi),
                          float(bid1), float(ask1), int(bid_qty), int(ask_qty)))
        if len(self._buf) >= self._flush_n:
            self.flush()

    def _partition(self) -> Path:
        day = datetime.datetime.now(_IST).strftime("%Y-%m-%d")
        p = self.data_dir / f"date={day}"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def flush(self) -> Path | None:
        if not self._buf:
            return None
        df = pd.DataFrame(self._buf, columns=_COLUMNS)
        self._buf.clear()
        self._seq += 1
        out = self._partition() / f"ticks_{self._seq:06d}.parquet"
        df.to_parquet(out, engine="pyarrow", compression="zstd", index=False)
        return out

    def close(self) -> None:
        self.flush()
