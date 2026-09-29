"""Append-only tick capture.

Microstructure features (OBI, CVD, whale CVD, session VWAP, intraday POC) are
40-50% of the composite score and are derived from data that was previously
consumed and discarded. Without this file they can never be validated.

Volume: ~2.4M rows/day for 27 symbols; ~40-60 MB/day compressed.
"""
from __future__ import annotations

import datetime
import threading
import time
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
        # record() runs on the event loop, flush() on a worker thread
        # (asyncio.to_thread). Building the frame from a list still being
        # appended to raised "Length of values (2918) does not match length of
        # index (2929)" and killed the flush task (2026-09-29), and clear()
        # after the copy dropped ticks. The buffer is swapped under a lock.
        self._lock = threading.Lock()
        # File names carry this process's start time: the sequence restarts
        # at 1 in every process, and a restarted feed overwrote the morning's
        # ticks_000001..4 (2026-09-29). The stamp also keeps names in time order.
        self._run = f"{time.time_ns() // 1_000_000:013d}"

    def record(self, token: str, ts_ms: int, ltp: float, vtt: float, oi: float,
               bid1: float, ask1: float, bid_qty: int, ask_qty: int) -> None:
        row = (token, int(ts_ms), float(ltp), float(vtt), float(oi),
               float(bid1), float(ask1), int(bid_qty), int(ask_qty))
        with self._lock:
            self._buf.append(row)
            full = len(self._buf) >= self._flush_n
        if full:
            self.flush()

    def _partition(self) -> Path:
        day = datetime.datetime.now(_IST).strftime("%Y-%m-%d")
        p = self.data_dir / f"date={day}"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def flush(self) -> Path | None:
        with self._lock:
            if not self._buf:
                return None
            buf, self._buf = self._buf, []
            self._seq += 1
            seq = self._seq
        df = pd.DataFrame(buf, columns=_COLUMNS)
        out = self._partition() / f"ticks_{self._run}_{seq:06d}.parquet"
        while out.exists():                         # never overwrite a written part
            seq += 1000
            out = out.with_name(f"ticks_{self._run}_{seq:06d}.parquet")
        df.to_parquet(out, engine="pyarrow", compression="zstd", index=False)
        return out

    def close(self) -> None:
        self.flush()
