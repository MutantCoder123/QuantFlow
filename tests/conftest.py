"""Shared pytest configuration.

The production modules use flat imports (``from technical_engine import MathEngine``)
and rely on ``trading_copilot/`` being on sys.path. New packages under
``trading_copilot/core`` and ``trading_copilot/journal`` are imported as
``core.x`` / ``journal.x``. Both work once trading_copilot/ is on the path.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COPILOT = ROOT / "trading_copilot"

for _p in (str(ROOT), str(COPILOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_watchlist(monkeypatch, tmp_path_factory):
    """Tests never read the operator's watchlist.csv: removing a stock there
    must not change a test's result. With no file, every stock counts as on
    the list (core.watchlist fails open); tests that need a list pass a path."""
    from core import watchlist as wl
    missing = tmp_path_factory.getbasetemp() / "no-watchlist.csv"
    monkeypatch.setattr(wl, "_path", lambda: missing)
    monkeypatch.setattr(wl, "_cache", {"mtime": None, "symbols": None})
