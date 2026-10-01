"""scripts/fit_score.py: the skill measures and the leave-one-day-out fit.
Synthetic data: one input drives the forward return, one is noise."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "trading_copilot" / "scripts"))
import fit_score  # noqa: E402


def rows(days=3, n=400, seed=1):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(days):
        good, noise = rng.normal(size=n), rng.normal(size=n)
        out.append(pd.DataFrame({"day": f"2026-10-0{i + 1}", "good": good * 1e6, "noise": noise,
                                 "fwd": 0.3 * good + rng.normal(size=n)}))
    return pd.concat(out, ignore_index=True)


def test_ic_is_a_rank_correlation_and_needs_a_sample():
    r = rows(days=1)
    assert fit_score.ic(r.good, r.fwd) > 0.2
    assert abs(fit_score.ic(r.noise, r.fwd)) < 0.1
    assert np.isnan(fit_score.ic(r.good.head(10), r.fwd.head(10)))


def test_the_fit_finds_the_real_input_on_unseen_days_whatever_its_scale():
    oos, w = fit_score.leave_one_day_out(rows(), ["good", "noise"])
    assert len(oos) == 3 and all(v > 0.15 for v in oos.values())
    assert w["good"] > 0 and abs(w["good"]) > 5 * abs(w["noise"])
