"""Shared value objects for the L3 risk layer (improved §4.3)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Proposal:
    """An authorised entry idea from L1/L2, before sizing."""
    symbol: str
    bias: str                 # "LONG" | "SHORT"
    entry: float
    stop: float
    target: float
    composite: float
    regime: str


@dataclass(frozen=True)
class SizedProposal:
    """A Proposal that cleared the risk layer, now with a quantity."""
    symbol: str
    bias: str
    entry: float
    stop: float
    target: float
    composite: float
    regime: str
    qty: int
    risk_amount: float        # qty * |entry - stop|, in rupees


@dataclass(frozen=True)
class Rejection:
    """A Proposal the risk layer refused to size."""
    reason: str
    qty: int = 0
