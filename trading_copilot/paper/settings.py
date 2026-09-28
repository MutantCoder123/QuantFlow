"""Paper account settings.

Resolution: risk.yaml (capital and risk limits) and paper.yaml (slippage and
engine options) supply defaults; paper_overrides.yaml -- written only by the
dashboard -- overrides the EDITABLE fields. risk.yaml is the live risk config
and is never written from here.
"""
from __future__ import annotations

import math
from pathlib import Path

import yaml

from core.risk import RiskLimits

# field -> (default source, (lo, hi, lo_inclusive), plain-language rule)
EDITABLE = {
    "capital":              ("risk.yaml",  (0, None, False), "must be more than ₹0"),
    "risk_per_trade_pct":   ("risk.yaml",  (0, 10, False),   "must be above 0% and at most 10%"),
    "max_daily_loss_pct":   ("risk.yaml",  (0, 50, False),   "must be above 0% and at most 50%"),
    "max_cluster_risk_pct": ("risk.yaml",  (0, 100, False),  "must be above 0% and at most 100%"),
    "slippage_pct":         ("paper.yaml", (0, 1, True),     "must be between 0% and 1%"),
}


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


class PaperSettings:
    def __init__(self, risk_path: Path | None = None, paper_path: Path | None = None,
                 overrides_path: Path | None = None):
        from paths import CONFIG_DIR
        self.risk_path = risk_path or CONFIG_DIR / "risk.yaml"
        self.paper_path = paper_path or CONFIG_DIR / "paper.yaml"
        self.overrides_path = overrides_path or CONFIG_DIR / "paper_overrides.yaml"

    # -- reading -----------------------------------------------------------
    def _defaults(self) -> dict:
        risk, paper = _load(self.risk_path), _load(self.paper_path)
        return {
            "capital": float(risk["capital"]),
            "risk_per_trade_pct": float(risk.get("risk_per_trade_pct", 0.5)),
            "max_daily_loss_pct": float(risk.get("max_daily_loss_pct", 2.0)),
            "max_cluster_risk_pct": float(risk.get("max_cluster_risk_pct", 1.0)),
            "max_adv_participation": float(risk.get("max_adv_participation", 0.02)),
            "slippage_pct": float((paper.get("fill") or {}).get("slippage_pct", 0.03)),
        }

    def defaults(self) -> dict:
        """What each editable field reverts to on "use default"."""
        d = self._defaults()
        return {k: d[k] for k in EDITABLE}

    def options(self) -> dict:
        """Non-editable engine options from paper.yaml."""
        p = _load(self.paper_path)
        return {
            "enabled": bool(p.get("enabled", True)),
            "autonomous": bool(p.get("autonomous", True)),
            "judge_model": str(p.get("judge_model", "gemini-2.5-flash")),
            "stale_price_seconds": float(p.get("stale_price_seconds", 15)),
            "equity_snapshot_seconds": float(p.get("equity_snapshot_seconds", 60)),
            "metrics_min_n": dict(p.get("metrics_min_n") or {}),
        }

    def overrides(self) -> dict:
        return {k: v for k, v in _load(self.overrides_path).items() if k in EDITABLE}

    def effective(self) -> dict:
        eff = self._defaults()
        eff.update({k: float(v) for k, v in self.overrides().items()})
        return eff

    def sources(self) -> dict:
        ov = self.overrides()
        return {k: ("override" if k in ov else src) for k, (src, _, _) in EDITABLE.items()}

    def risk_limits(self) -> RiskLimits:
        e = self.effective()
        return RiskLimits(capital=e["capital"], risk_per_trade_pct=e["risk_per_trade_pct"],
                          max_daily_loss_pct=e["max_daily_loss_pct"],
                          max_cluster_risk_pct=e["max_cluster_risk_pct"],
                          max_adv_participation=e["max_adv_participation"])

    # -- writing -----------------------------------------------------------
    @staticmethod
    def _check(field: str, value) -> str | None:
        if field not in EDITABLE:
            return "not an editable setting"
        if value is None:
            return None                                   # reset to default
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return "must be a number"
        if not math.isfinite(value):
            return "must be a number"
        _, (lo, hi, lo_incl), rule = EDITABLE[field]
        if (value < lo) or (value == lo and not lo_incl) or (hi is not None and value > hi):
            return rule
        return None

    def update(self, changes: dict) -> tuple[bool, dict]:
        """Apply all changes or none. None for a field resets it to default.
        Returns (ok, {field: reason})."""
        errors = {f: r for f, v in changes.items() if (r := self._check(f, v))}
        if errors:
            return False, errors
        ov = self.overrides()
        for f, v in changes.items():
            if v is None:
                ov.pop(f, None)
            else:
                ov[f] = v
        self.overrides_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.overrides_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(ov, f, sort_keys=True)
        return True, {}
