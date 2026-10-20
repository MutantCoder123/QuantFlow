"""Rebuild data/tod_vol.json (policy v6 time-of-day volatility) from the 1-minute store.

Run as a script from here, not core/tod_vol.py: running a file inside core/ puts
core/ on sys.path, and core/types.py then shadows the standard library's types.

usage: python trading_copilot/scripts/build_tod_vol.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.tod_vol import build  # noqa: E402

if __name__ == "__main__":
    print(build())
