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


# Below this spread (percentage points) between the best and worst populated
# bucket, the score is not separating winners from losers in any useful way.
_DISCRIMINATION_THRESHOLD_PP = 10.0


def reliability_buckets(signals: list, primary_minute: int = 90,
                        n_buckets: int = 10, min_n: int = 10,
                        measure_at=None) -> dict:
    """Realised win rate per |composite| decile (Task 4.4).

    The x-axis is |composite| rather than a predicted probability because
    while uncalibrated there IS no prediction -- and this is exactly the
    diagnostic Task 4.3 needs first: a flat curve means the score has no
    discriminative power, and no amount of calibration fixes that. Buckets
    thinner than `min_n` are marked suppressed (not silently dropped) so the
    operator sees where the evidence runs out.
    """
    from core.outcome_schema import primary_outcome

    # `primary_minute` stays the caller-facing knob and the reported x-axis
    # horizon; `measure_at` is the full checkpoint list that decides whether a
    # record belongs to this horizon config at all. Left None it comes from the
    # policy config, whose largest entry IS primary_minute.
    width = 1.0 / n_buckets
    buckets = [{"lo": round(i * width, 4), "hi": round((i + 1) * width, 4),
                "n": 0, "wins": 0} for i in range(n_buckets)]

    n_resolved = 0
    n_legacy_excluded = 0
    for s in signals or []:
        outcome = s.get("outcome") or {}
        if not str(outcome.get("status", "")).startswith("RESOLVED"):
            continue
        po = primary_outcome(s, measure_at)
        if po is None:
            # Resolved, but not attributable to the horizon config in force --
            # graded at a retired checkpoint (the pre-Task-3.4 30m/60m schema)
            # or written by the pre-C-3 resolver. Counted separately rather
            # than folded in: a 60m outcome is not a 90m outcome, and silently
            # mixing them would corrupt the curve. Counted rather than
            # dropped, so "no data" is never shown when data exists.
            #
            # A trade stopped out or target-hit EARLY is NOT in this bucket --
            # it is closed, so its hit is its primary-horizon outcome
            # (core.outcome_schema). Excluding those would have left the curve
            # drawn only from trades that hit neither stop nor target.
            n_legacy_excluded += 1
            continue
        try:
            x = abs(float((s.get("signal_snapshot") or {}).get("composite_score") or 0.0))
        except (TypeError, ValueError):
            continue
        idx = min(int(x / width), n_buckets - 1)
        buckets[idx]["n"] += 1
        buckets[idx]["wins"] += 1 if po["directional_correct"] else 0
        n_resolved += 1

    populated = []
    for b in buckets:
        b["suppressed"] = b["n"] < min_n
        if b["n"] and not b["suppressed"]:
            b["win_rate"] = round(b["wins"] / b["n"] * 100, 2)
            populated.append(b["win_rate"])
        else:
            b["win_rate"] = None
        # The diagonal a perfectly calibrated score would sit on.
        b["reference"] = round((b["lo"] + b["hi"]) / 2 * 100, 2)

    spread = round(max(populated) - min(populated), 2) if populated else None
    return {
        "buckets": buckets,
        "n_resolved": n_resolved,
        "n_legacy_excluded": n_legacy_excluded,
        "populated_buckets": len(populated),
        "win_rate_spread": spread,
        "has_discriminative_power": (
            spread is not None and spread >= _DISCRIMINATION_THRESHOLD_PP),
        "min_n": min_n,
        "primary_minute": primary_minute,
    }
