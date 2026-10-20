"""The bare stock name from any form the system meets it in.

"SAIL-EQ", "NSE_EQ|SAIL", " sail " -> "SAIL". Only an NSE series suffix is
removed: "BAJAJ-AUTO" and "NAM-INDIA" are whole names. Cutting at the first
hyphen (the old way, in ~25 places) turned BAJAJ-AUTO into "BAJAJ", which has
no stored history or baseline, so the stock sat at DATA_INCOMPLETE.
"""
from __future__ import annotations

# NSE series codes that can trail a trading symbol
SERIES = frozenset({"EQ", "BE", "BZ", "BL", "SM", "ST", "GB", "GS", "IL", "RL", "N1", "N2", "N3"})


def base_symbol(s) -> str:
    t = str(s or "").split("|")[-1].strip().upper()
    head, sep, tail = t.rpartition("-")
    return head if sep and head and tail in SERIES else t
