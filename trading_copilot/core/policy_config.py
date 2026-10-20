"""Load and validate the versioned decision-policy YAML.

Every threshold that used to be a hardcoded constant scattered across
semantic_tagger.py, conviction_scorer.py, intraday_gatekeeper.py, and
regime_manager.py now lives in one auditable, versioned file
(config/policy_v1.yaml), transcribed exactly from the live code so v1 is a
refactor, not a retune.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_REQUIRED = ("semantic", "regime", "conviction", "gates", "horizon")


@dataclass(frozen=True)
class PolicyConfig:
    version: int | float          # 5, 6 ... or a point release such as 5.1
    semantic: dict[str, Any]
    regime: dict[str, Any]
    conviction: dict[str, Any]
    gates: dict[str, Any]
    horizon: dict[str, Any]
    books: dict[str, Any] = field(default_factory=dict)   # v8: books.intraday / books.auction

    @classmethod
    def from_dict(cls, d: dict) -> "PolicyConfig":
        missing = [k for k in _REQUIRED if not d.get(k)]
        if missing:
            raise ValueError(f"policy config missing required section(s): {missing}")
        v = float(d["version"])
        return cls(version=int(v) if v.is_integer() else v, semantic=d["semantic"],
                   regime=d["regime"], conviction=d["conviction"],
                   gates=d["gates"], horizon=d["horizon"], books=d.get("books") or {})


DEFAULT_POLICY = "policy_v8_1.yaml"   # v8.1: alpha intraday trades; auction book as research (V8_CHANGES.md)


@lru_cache(maxsize=4)
def load_policy(path: Path | None = None) -> PolicyConfig:
    """The policy in force: DEFAULT_POLICY, or the file named by the
    QUANTFLOW_POLICY environment variable (e.g. policy_v3.yaml to step back
    one version without a code change)."""
    if path is None:
        import os
        from paths import CONFIG_DIR
        path = CONFIG_DIR / (os.environ.get("QUANTFLOW_POLICY") or DEFAULT_POLICY)
    with open(path, "r", encoding="utf-8") as f:
        return PolicyConfig.from_dict(yaml.safe_load(f))
