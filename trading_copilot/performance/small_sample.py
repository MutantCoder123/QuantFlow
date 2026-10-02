"""Early estimates: what the trades so far say, with how sure we can be.

The headline rates (performance.metrics) stay hidden until `min_n` trades,
so a lucky first few never read as fact. These work from the first trade
because each one carries its uncertainty:

  win_rate       Bayesian, uniform Beta(1, 1) prior: posterior mean
                 (wins + 1) / (n + 2), and the central 90% credible range.
  expectancy_r   mean R per trade, 90% range from Student's t (n >= 2).
  p_edge         probability the true mean R is above zero, from the same t
                 distribution: "how likely is it this is a real edge?"
  profit_factor  observed, with a 90% range from 2,000 bootstrap resamples
                 (fixed seed: the same trades always give the same range).

Every figure is {value, low, high, n, method}; low/high are None where the
sample cannot support a range (one trade, or no spread at all).
"""
from __future__ import annotations

import math
import random
from statistics import mean, stdev

from scipy import stats

LEVEL = 0.90                 # central range shown in the UI
_LO, _HI = (1 - LEVEL) / 2, 1 - (1 - LEVEL) / 2
BOOT = 2000
SEED = 20261001


def _fig(value, low, high, n, method):
    clean = lambda x: None if x is None or (isinstance(x, float) and not math.isfinite(x)) else x
    return {"value": clean(value), "low": clean(low), "high": clean(high), "n": n, "method": method}


def win_rate(nets: list[float]) -> dict:
    n = len(nets)
    w = sum(1 for v in nets if v > 0)
    a, b = w + 1, n - w + 1
    return _fig((a / (a + b)) * 100, stats.beta.ppf(_LO, a, b) * 100, stats.beta.ppf(_HI, a, b) * 100,
                n, "Bayesian, uniform prior")


def expectancy_r(rs: list[float]) -> dict:
    n = len(rs)
    if n == 0:
        return _fig(None, None, None, 0, "t interval")
    m = mean(rs)
    if n < 2:
        return _fig(m, None, None, n, "t interval")
    se = stdev(rs) / math.sqrt(n)
    if se == 0:
        return _fig(m, m, m, n, "t interval")
    t = stats.t.ppf(_HI, n - 1)
    return _fig(m, m - t * se, m + t * se, n, "t interval")


def p_edge(rs: list[float]) -> dict:
    """P(true mean R > 0), as a percentage."""
    n = len(rs)
    if n < 2:
        return _fig(None, None, None, n, "t distribution")
    m, sd = mean(rs), stdev(rs)
    if sd == 0:
        return _fig(100.0 if m > 0 else 0.0, None, None, n, "t distribution")
    return _fig(stats.t.cdf(m / (sd / math.sqrt(n)), n - 1) * 100, None, None, n, "t distribution")


def profit_factor(nets: list[float]) -> dict:
    n = len(nets)
    def pf(xs):
        loss = -sum(v for v in xs if v <= 0)
        return sum(v for v in xs if v > 0) / loss if loss > 0 else None
    obs = pf(nets)
    if n < 2:
        return _fig(obs, None, None, n, "bootstrap")
    rng = random.Random(SEED)
    draws = sorted(x for x in (pf([nets[rng.randrange(n)] for _ in range(n)]) for _ in range(BOOT)) if x is not None)
    if len(draws) < BOOT // 2:              # mostly no losing trade in the resamples: no range
        return _fig(obs, None, None, n, "bootstrap")
    return _fig(obs, draws[int(_LO * len(draws))], draws[int(_HI * len(draws)) - 1], n, "bootstrap")


def early(trades: list[dict]) -> dict:
    nets = [t["net"] for t in trades]
    rs = [t["r_net"] for t in trades if t.get("r_net") is not None]
    return {"level": LEVEL, "win_rate": win_rate(nets), "expectancy_r": expectancy_r(rs),
            "p_edge": p_edge(rs), "profit_factor": profit_factor(nets)}
