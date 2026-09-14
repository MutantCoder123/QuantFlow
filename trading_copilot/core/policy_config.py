"""Load and validate the versioned decision-policy YAML.

Every threshold that used to be a hardcoded constant scattered across
semantic_tagger.py, conviction_scorer.py, intraday_gatekeeper.py, and
regime_manager.py now lives in one auditable, versioned file
(config/policy_v1.yaml), transcribed exactly from the live code so v1 is a
refactor, not a retune.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_REQUIRED = ("semantic", "regime", "conviction", "gates", "horizon")


@dataclass(frozen=True)
class PolicyConfig:
    version: int
    semantic: dict[str, Any]
    regime: dict[str, Any]
    conviction: dict[str, Any]
    gates: dict[str, Any]
    horizon: dict[str, Any]

    @classmethod
    def from_dict(cls, d: dict) -> "PolicyConfig":
        missing = [k for k in _REQUIRED if not d.get(k)]
        if missing:
            raise ValueError(f"policy config missing required section(s): {missing}")
        return cls(version=int(d["version"]), semantic=d["semantic"],
                   regime=d["regime"], conviction=d["conviction"],
                   gates=d["gates"], horizon=d["horizon"])


@lru_cache(maxsize=4)
def load_policy(path: Path | None = None) -> PolicyConfig:
    if path is None:
        from paths import CONFIG_DIR
        path = CONFIG_DIR / "policy_v1.yaml"
    with open(path, "r", encoding="utf-8") as f:
        return PolicyConfig.from_dict(yaml.safe_load(f))
