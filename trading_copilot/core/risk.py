"""L3 risk layer (improved §4.3): position sizing, liquidity and cluster
exposure limits.

The pipeline emits entry/stop/target with no quantity and treats seven
correlated LONGs as seven independent bets. This module turns a Proposal
into a SizedProposal (with qty and rupee risk) or a Rejection, enforcing:
per-trade risk as a fraction of capital, a liquidity cap on ADV
participation, an aggregate per-cluster risk budget, and a daily-loss
circuit breaker.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from core.types import Proposal, Rejection, SizedProposal


# --------------------------------------------------------------------------
# Cluster map (Task 3.2)
# --------------------------------------------------------------------------
@lru_cache(maxsize=4)
def load_clusters(path: Path | None = None) -> dict:
    """Return {symbol: cluster_id}, inverted from the cluster->members YAML."""
    if path is None:
        from paths import CONFIG_DIR
        path = CONFIG_DIR / "clusters.yaml"
    with open(path, "r", encoding="utf-8") as f:
        groups = yaml.safe_load(f) or {}
    inv: dict[str, str] = {}
    for cluster_id, members in groups.items():
        for sym in (members or []):
            inv[str(sym).upper()] = str(cluster_id)
    return inv


def cluster_of(symbol: str, clusters: dict) -> str:
    """The cluster id for `symbol`, or the symbol itself when unmapped."""
    return clusters.get(str(symbol).upper(), str(symbol).upper())


# --------------------------------------------------------------------------
# Risk limits + portfolio (Task 3.3)
# --------------------------------------------------------------------------
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RiskLimits:
    capital: float
    risk_per_trade_pct: float = 0.5       # of capital, measured at the stop
    max_daily_loss_pct: float = 2.0
    max_cluster_risk_pct: float = 1.0     # aggregate across correlated names
    max_adv_participation: float = 0.02   # never size beyond 2% of 20d avg volume
    # Position value caps, as multiples of capital. Risk-based sizing alone
    # let a tight stop buy anything: on 2026-09-29 four paper positions held
    # Rs 64 lakh at once on Rs 10 lakh capital.
    max_position_value_x: float = 1.0     # one position's value <= capital x this
    max_open_value_x: float = 5.0         # all open positions' value <= capital x this
    # A trade squeezed by the caps into a sliver of its normal risk is not
    # worth its charges: on 2026-09-29 the last Rs 255 of open-value room
    # bought 1 SAIL. Below this fraction of the per-trade risk, skip it.
    min_trade_risk_frac: float = 0.1


@dataclass
class Portfolio:
    realized_loss_today: float = 0.0
    _open: list = field(default_factory=list)   # [{symbol, cluster, risk_amount, value}]

    def add_open(self, symbol: str, cluster: str, risk_amount: float, value: float = 0.0) -> None:
        self._open.append({"symbol": symbol, "cluster": cluster,
                           "risk_amount": float(risk_amount), "value": float(value)})

    def risk_in_cluster(self, cluster: str) -> float:
        return sum(p["risk_amount"] for p in self._open if p["cluster"] == cluster)

    def open_value(self) -> float:
        """Rupee value (qty x entry) of every open position."""
        return sum(p["value"] for p in self._open)


@lru_cache(maxsize=4)
def load_risk_limits(path: Path | None = None) -> RiskLimits:
    if path is None:
        from paths import CONFIG_DIR
        path = CONFIG_DIR / "risk.yaml"
    with open(path, "r", encoding="utf-8") as f:
        d = yaml.safe_load(f) or {}
    return RiskLimits(
        capital=float(d["capital"]),
        risk_per_trade_pct=float(d.get("risk_per_trade_pct", 0.5)),
        max_daily_loss_pct=float(d.get("max_daily_loss_pct", 2.0)),
        max_cluster_risk_pct=float(d.get("max_cluster_risk_pct", 1.0)),
        max_adv_participation=float(d.get("max_adv_participation", 0.02)),
        max_position_value_x=float(d.get("max_position_value_x", 1.0)),
        max_open_value_x=float(d.get("max_open_value_x", 5.0)),
        min_trade_risk_frac=float(d.get("min_trade_risk_frac", 0.1)),
    )


def size(p: Proposal, book: Portfolio, lim: RiskLimits,
         adv_shares: float) -> SizedProposal | Rejection:
    """Turn a Proposal into a SizedProposal or a Rejection (improved §4.3)."""
    risk_per_share = abs(p.entry - p.stop)
    if risk_per_share <= 0:
        return Rejection("DEGENERATE_STOP")

    # Daily-loss circuit breaker: checked first, rejects outright.
    if book.realized_loss_today >= lim.capital * lim.max_daily_loss_pct / 100.0:
        return Rejection("DAILY_LOSS_LIMIT")

    qty = int((lim.capital * lim.risk_per_trade_pct / 100.0) / risk_per_share)
    qty = min(qty, int(adv_shares * lim.max_adv_participation))   # liquidity cap

    cluster = cluster_of(p.symbol, load_clusters())
    open_risk = book.risk_in_cluster(cluster)
    headroom = lim.capital * lim.max_cluster_risk_pct / 100.0 - open_risk
    if headroom <= 0:
        return Rejection(f"CLUSTER_LIMIT_{cluster}")
    qty = min(qty, int(headroom / risk_per_share))

    # Value caps: one position, then everything open together.
    room = lim.capital * lim.max_open_value_x - book.open_value()
    if room <= 0:
        return Rejection("OPEN_VALUE_LIMIT")
    qty = min(qty, int(min(lim.capital * lim.max_position_value_x, room) / p.entry))

    if qty <= 0:
        return Rejection("SIZE_ROUNDS_TO_ZERO")
    budget = lim.capital * lim.risk_per_trade_pct / 100.0
    if qty * risk_per_share < budget * lim.min_trade_risk_frac:
        return Rejection("SIZE_TOO_SMALL")

    return SizedProposal(
        symbol=p.symbol, bias=p.bias, entry=p.entry, stop=p.stop,
        target=p.target, composite=p.composite, regime=p.regime,
        qty=qty, risk_amount=qty * risk_per_share)
