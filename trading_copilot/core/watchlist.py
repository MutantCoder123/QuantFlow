"""Is a stock on the watchlist right now?

watchlist.csv is the operator's choice. Removing a stock there used to change
nothing until a restart: the feed applies additions live but defers removals
(a held position must keep its monitoring), and the decision loop evaluates
every stock the feed streams -- so a removed stock kept producing signals and
paper trades. The decision loop and the paper account ask here instead.

    symbols() -> frozenset of bare symbols ("SAIL"), re-read when the file changes
    on_watchlist(symbol) -> bool

A read that fails or finds no rows (a write in progress) keeps the last good
set; with none yet, every stock is treated as on the list rather than
blocking trading on a read error.
"""
from __future__ import annotations

import csv
import os
import threading
from core.symbols import base_symbol

_lock = threading.Lock()
_cache: dict = {"mtime": None, "symbols": None}


def _bare(s: str) -> str:
    return base_symbol(s)


def _path():
    from paths import WATCHLIST_PATH
    return WATCHLIST_PATH


def symbols(path=None) -> frozenset | None:
    path = path or _path()
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return _cache["symbols"]
    with _lock:
        if _cache["mtime"] == mtime and _cache["symbols"] is not None:
            return _cache["symbols"]
        try:
            with open(path, newline="", encoding="utf-8") as f:
                got = frozenset(_bare(r.get("Symbol")) for r in csv.DictReader(f) if r.get("Symbol"))
        except (OSError, csv.Error, UnicodeDecodeError):
            return _cache["symbols"]
        if got:
            _cache.update(mtime=mtime, symbols=got)
        return _cache["symbols"]


def on_watchlist(symbol: str, path=None) -> bool:
    current = symbols(path)
    return True if current is None else _bare(symbol) in current
