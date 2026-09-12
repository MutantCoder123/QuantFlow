"""Measured probability, or none at all (improved §4.2, fixes C-1).

`implied_probability` used to be:

    raw  = 1 / (1 + exp(-(|composite| * 4.5)))
    p    = 0.50 + (raw - 0.50) * 0.85

That is not a probability. It is a monotone rescaling of the composite score
with two magic constants, never fitted against a single realised outcome --
yet it was rendered to the operator as a confidence number and handed to the
LLM as "ground truth". A system that has not measured its hit rate must say
so; inventing 64% is strictly worse than admitting nothing is known.

This module holds the fitted model when one exists (Task 4.3, data-gated at
MIN_N resolved outcomes) and returns None until then.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Below this many resolved outcomes, any fit is noise -- refuse to predict.
MIN_N = 200


@dataclass(frozen=True)
class Calibration:
    b0: float
    b1: float
    n_resolved: int
    fitted_at: str
    buckets: list = field(default_factory=list)   # decile reliability buckets

    @property
    def is_usable(self) -> bool:
        return self.n_resolved >= MIN_N

    def predict(self, composite: float) -> float | None:
        """P(win) for |composite|, or None when the fit isn't trustworthy."""
        if not self.is_usable:
            return None
        try:
            z = self.b0 + self.b1 * abs(float(composite))
        except (TypeError, ValueError):
            return None
        return 1.0 / (1.0 + math.exp(-z))


def load_calibration(path: Path | None = None) -> Calibration | None:
    """Load data/calibration.json, or None when it hasn't been fitted yet."""
    if path is None:
        from paths import DATA_DIR
        path = DATA_DIR / "calibration.json"
    path = Path(path)
    if not path.is_file():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return Calibration(
            b0=float(d["b0"]), b1=float(d["b1"]),
            n_resolved=int(d.get("n_resolved", 0)),
            fitted_at=str(d.get("fitted_at", "")),
            buckets=d.get("buckets", []) or [],
        )
    except Exception as e:
        logger.error(f"Ignoring unreadable calibration.json: {e}")
        return None


def implied_probability(composite: float, regime: str,
                        cal: Calibration | None = None) -> float | None:
    """The measured P(win), or None when nothing has been measured.

    `regime` is accepted now so a future per-regime fit is a drop-in change
    at this one call site rather than a signature break everywhere.
    """
    if cal is None:
        return None
    return cal.predict(composite)


def calibration_status(cal: Calibration | None = None) -> str:
    """Human-readable state for the UI, e.g. 'unmeasured (n=0 < 200)'."""
    n = cal.n_resolved if cal is not None else 0
    if cal is not None and cal.is_usable:
        return f"calibrated (n={n}, fitted {cal.fitted_at})"
    return f"unmeasured (n={n} < {MIN_N})"
