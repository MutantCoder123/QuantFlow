"""Atomic file writes for state other processes read.

`open(path, "w")` truncates first and fills later, so a reader in another
process -- or the next start after a crash -- can see an empty or half-written
file. On 2026-09-29 the feed logged "Failed to reload institutional flow
state: Expecting value" while the macro worker was rewriting it. Writing a
temp file beside the target and swapping it in with os.replace means readers
see the old file or the new one, never a torn one.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path


def write_json_atomic(path, obj, retries: int = 20, **dump_kwargs) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, **dump_kwargs)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(retries):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                # Windows refuses the swap while a reader has the file open;
                # readers hold it for milliseconds.
                if attempt == retries - 1:
                    raise
                time.sleep(0.05)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
