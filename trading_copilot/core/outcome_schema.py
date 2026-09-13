"""One shared reading of a signal's outcome at the horizon in force.

Two views needed the same judgement -- the reliability curve
(core.calibration) and the session review (performance_analyzer) -- and both
had reached for the same shortcut: "does the record carry a
``directional_correct_{primary}m`` key?". That proxy is narrower than the
invariant, and the gap is not academic.

``SignalLedger._resolve_one`` breaks out of the checkpoint loop the moment a
stop or target is touched, stamping ``RESOLVED_EARLY``. A signal whose target
was hit at the 30m checkpoint therefore never gets a 90m key -- under the
CURRENT horizon config, not a retired one. The shortcut classed it "graded at
a retired horizon, no 90m outcome", which is false: the position is closed,
and that early hit IS the primary-horizon outcome. Worse, excluding exactly
the decided trades leaves a win rate computed only from signals that hit
neither stop nor target -- a survivorship-filtered sample presented as the
session's result (the C-3 defect class again).

The real invariant: a resolved record is excluded only when it cannot be
attributed to the horizon config in force.

Attribution, in priority order:

1. ``record["horizon"]["measure_at_minutes"]``, stamped at record time by
   ``SignalLedger.record_signal``. Authoritative; the durable answer for every
   record written from now on. Only its PRIMARY horizon (the largest entry) is
   compared -- adding or dropping a diagnostic checkpoint alongside an
   unchanged primary horizon retires nothing.
2. For older records, which carry no stamp, two markers date them:
   - any ``ltp_at_*`` key -- written only by the LTP-sampling resolver that
     the bar-accurate rewrite (C-3) replaced, and never by the current one;
   - failing an outcome at the primary horizon, any ``directional_correct_{m}m``
     for an ``m`` the config no longer measures (e.g. the retired 60m).

Everything excluded is COUNTED, never silently dropped: "no data" must never
be shown when data exists.
"""
from __future__ import annotations

PENDING = "PENDING"
LEGACY = "LEGACY"
MEASURED = "MEASURED"

_LEGACY_KEY_PREFIX = "ltp_at_"


def measure_at_minutes() -> list:
    """The checkpoints in force, from the single horizon config."""
    try:
        from core.policy_config import load_policy
        mins = load_policy().horizon.get("measure_at_minutes", [30, 90])
        return sorted({int(m) for m in mins})
    except Exception:
        return [30, 90]


def _mins(measure_at) -> list:
    return sorted({int(m) for m in (measure_at or measure_at_minutes())})


def _checkpoint_minutes(outcome: dict) -> set:
    """Every N for which the record carries a ``directional_correct_Nm``."""
    out = set()
    for k in outcome:
        if k.startswith("directional_correct_") and k.endswith("m"):
            try:
                out.add(int(k[len("directional_correct_"):-1]))
            except ValueError:
                continue
    return out


def _is_early_decided(outcome: dict) -> bool:
    """The stop or target was touched, so the position is closed.

    `SignalLedger._resolve_one` stamps RESOLVED_EARLY and breaks out of the
    checkpoint loop at that point, which is why no later checkpoint key exists.
    """
    return (str(outcome.get("status", "")) == "RESOLVED_EARLY"
            and bool(outcome.get("hit_stop") or outcome.get("hit_target")))


def _is_retired_schema(signal: dict, outcome: dict, mins: list) -> bool:
    """Was this record graded against a horizon we no longer measure at?

    Keyed off the PRIMARY horizon, not the full checkpoint list. A record
    measured at 90m is still measured at 90m whether or not a 15m diagnostic
    checkpoint was added alongside it, so `[30, 90] -> [15, 30, 90]` must not
    retire valid history. Comparing the checkpoint sets for equality would
    make any config edit report "graded at a retired horizon" about records
    measured at exactly the horizon still in force -- the same false statement
    the early-close fix removed, mirrored.
    """
    primary = mins[-1]

    stamped = (signal.get("horizon") or {}).get("measure_at_minutes")
    if stamped:
        # Stamped at record time: the stamp decides, and nothing else does.
        return max(int(m) for m in stamped) != primary

    if any(k.startswith(_LEGACY_KEY_PREFIX) for k in outcome):
        return True
    if f"directional_correct_{primary}m" in outcome:
        # Measured at the horizon in force; extra checkpoints it also carries
        # are diagnostics, not grounds for retiring it.
        return False
    return bool(_checkpoint_minutes(outcome) - set(mins))


def primary_outcome(signal: dict, measure_at=None) -> dict | None:
    """The signal's outcome at the horizon in force, or None if it has none.

    Returns ``{"directional_correct", "pnl_pct", "decided_at_min", "early"}``.

    ``directional_correct`` is taken from the checkpoint that decided the
    trade rather than re-derived from ``hit_target`` -- ``label_outcome``
    applies the round-trip cost floor there, and a target too close to clear
    costs is not a win.
    """
    outcome = signal.get("outcome") or {}
    if not str(outcome.get("status", "")).startswith("RESOLVED"):
        return None

    mins = _mins(measure_at)
    if _is_retired_schema(signal, outcome, mins):
        return None

    primary = mins[-1]
    if f"directional_correct_{primary}m" in outcome:
        m = primary
    elif _is_early_decided(outcome):
        # The trade closed before the primary checkpoint, so the checkpoint it
        # closed at IS its outcome. This shortcut is available ONLY to a
        # genuinely early-decided record: a plain RESOLVED carrying nothing at
        # the primary horizon was not decided early, it was graded under an
        # older schema, and reading its 30m outcome as a 90m one would mix
        # horizons -- exactly what the exclusion exists to prevent.
        reached = sorted(_checkpoint_minutes(outcome) & set(mins))
        if not reached:
            return None
        m = reached[-1]
    else:
        return None

    pnl = outcome.get(f"pnl_{m}m_pct")
    return {
        "directional_correct": bool(outcome.get(f"directional_correct_{m}m", False)),
        "pnl_pct": None if pnl is None else float(pnl),
        "decided_at_min": m,
        "early": m != mins[-1],
    }


def classify(signal: dict, measure_at=None) -> str:
    """PENDING | LEGACY | MEASURED."""
    outcome = signal.get("outcome") or {}
    if not str(outcome.get("status", "")).startswith("RESOLVED"):
        return PENDING
    return MEASURED if primary_outcome(signal, measure_at) is not None else LEGACY


def normalise(signal: dict, measure_at=None) -> dict | None:
    """A copy of `signal` whose outcome carries the primary-horizon keys.

    An early-decided trade's outcome is projected onto
    ``directional_correct_{primary}m`` / ``pnl_{primary}m_pct`` so the shared
    ``PerformanceAnalyzer._compute_metrics`` -- deliberately left untouched,
    since it also feeds the performance dashboard and the LLM feedback
    payload -- reads it without knowing about early resolution.

    Returns None when the signal has no primary-horizon outcome. The input is
    never mutated.
    """
    po = primary_outcome(signal, measure_at)
    if po is None:
        return None

    mins = _mins(measure_at)
    primary = mins[-1]

    out = dict(signal.get("outcome") or {})
    out[f"directional_correct_{primary}m"] = po["directional_correct"]
    if po["pnl_pct"] is not None:
        out[f"pnl_{primary}m_pct"] = po["pnl_pct"]

    copy = dict(signal)
    copy["outcome"] = out
    return copy
