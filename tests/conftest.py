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
