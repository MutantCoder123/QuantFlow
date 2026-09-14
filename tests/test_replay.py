"""Task 5.4: the replay runner must be a deterministic re-execution of the
live pipeline, not an approximation of it.

Two properties are asserted, and they are the whole point of the module:

  1. Determinism -- the same tick file replayed twice against the same policy
     produces a byte-identical FeatureRecord sequence. Anything that leaks
     wall-clock time or process-wide state into the pipeline breaks this.
  2. Threshold sensitivity -- changing `conviction.min_reward_risk` (the gate
     that is actually wired to policy config today, via
     ConvictionScorer._cfg_conviction) changes the decision sequence. If it
     does not, the config file is decoration and replay cannot be used to
     fit a threshold.
"""
import json
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
import yaml

from conviction_scorer import ConvictionScorerRegistry
from diagnostic_ui import TerminalDashboard
from microstructure_engine import MicrostructureEngine
from paths import CONFIG_DIR
from regime_manager import RegimeManagerRegistry
from replay.runner import replay
from rolling_state_engine import RollingStateEngine

_IST = ZoneInfo("Asia/Kolkata")
_COLUMNS = ["token", "ts_ms", "ltp", "vtt", "oi", "bid1", "ask1", "bid_qty", "ask_qty"]

TOKEN = "NSE_EQ|REPLAYCO"

# 2026-09-09 is a Wednesday. 09:20 -> 10:19:30 IST sits inside market hours,
# after the 09:15 open and well before the 13:45 entry cutoff, so the pipeline
# runs its real LIVE path rather than its market-closed short circuits.
_START = pd.Timestamp("2026-09-09 09:20:00", tz=_IST)
_N_TICKS = 120
_TICK_SECONDS = 30


def _tick_frame() -> pd.DataFrame:
    """A steadily-rising, bid-dominated, whale-sized tape.

    Sized to actually light up the scorer rather than merely exercise the
    plumbing: 60 minutes of ticks crosses twelve 5-minute boundaries (so
    _compute_symbol has bars to work with), each tick trades ~2 crore rupees
    at the ask (so whale CVD accumulates and its 60s-sampled slope clears the
    |slope| > 50 flow-divergence gate), and the book is 8:1 bid-heavy (OBI
    0.78 -> EXTREME_BID_DOMINANCE).
    """
    rows = []
    vtt = 0.0
    for i in range(_N_TICKS):
        ts = _START + pd.Timedelta(seconds=_TICK_SECONDS * i)
        ltp = round(1000.0 + 0.25 * i, 2)
        vtt += 20_000.0
        rows.append((TOKEN, int(ts.value // 1_000_000), ltp, vtt, 0.0,
                     round(ltp - 0.05, 2), ltp, 8000, 1000))
    return pd.DataFrame(rows, columns=_COLUMNS)


@pytest.fixture
def tick_file(tmp_path):
    out = tmp_path / "date=2026-09-09"
    out.mkdir()
    path = out / "ticks_000001.parquet"
    _tick_frame().to_parquet(path, engine="pyarrow", index=False)
    return path


def _policy_with_min_rr(tmp_path, value: float, name: str):
    """policy_v1 with exactly one value changed, so any behaviour difference
    is attributable to that value alone."""
    with open(CONFIG_DIR / "policy_v1.yaml", "r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    doc["conviction"]["min_reward_risk"] = value
    path = tmp_path / name
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(doc, f)
    return path


@pytest.fixture(autouse=True)
def no_global_leak():
    """replay() restores every process-wide singleton it touches; this proves
    it, and stops a regression there from poisoning unrelated tests that run
    later in the same pytest process."""
    before = {
        "active_states": dict(TerminalDashboard.active_states),
        "catalyst_cache": dict(TerminalDashboard.catalyst_cache),
        "global_market_context": TerminalDashboard.global_market_context,
        "live_options_state": dict(RollingStateEngine.live_options_state),
        "daily_metrics_cache": dict(RollingStateEngine.daily_metrics_cache),
        "scorers": dict(ConvictionScorerRegistry._scorers),
        "managers": dict(RegimeManagerRegistry._managers),
        "micro": {n: dict(getattr(MicrostructureEngine, n))
                  for n in ("cvd_state", "vol_profile_state", "session_vwap_state",
                            "whale_cvd_state", "whale_cvd_history", "last_vtt_state",
                            "last_bba_state", "session_date")},
    }
    yield
    assert TerminalDashboard.active_states == before["active_states"]
    assert TerminalDashboard.catalyst_cache == before["catalyst_cache"]
    assert TerminalDashboard.global_market_context is before["global_market_context"]
    assert RollingStateEngine.live_options_state == before["live_options_state"]
    assert RollingStateEngine.daily_metrics_cache == before["daily_metrics_cache"]
    assert ConvictionScorerRegistry._scorers == before["scorers"]
    assert RegimeManagerRegistry._managers == before["managers"]
    for name, snap in before["micro"].items():
        assert getattr(MicrostructureEngine, name) == snap, name


def _rows(records):
    """Serialise to the byte-level form FeatureLog would persist. json.dumps
    renders NaN as the literal `NaN`, so a NaN feature compares equal to
    itself here -- dataclass equality would report it unequal and call a
    deterministic run non-deterministic."""
    return [json.dumps(r.to_row(), sort_keys=True, default=str) for r in records]


def test_replay_is_deterministic(tick_file):
    first = replay([tick_file], cfg=CONFIG_DIR / "policy_v1.yaml", speed=0)
    second = replay([tick_file], cfg=CONFIG_DIR / "policy_v1.yaml", speed=0)

    assert first, "replay produced no FeatureRecords -- the tape never reached _compute_symbol"
    assert _rows(first) == _rows(second)


def test_a_changed_threshold_changes_the_decision_sequence(tick_file, tmp_path):
    lenient = _policy_with_min_rr(tmp_path, 0.0, "policy_lenient.yaml")
    strict = _policy_with_min_rr(tmp_path, 100.0, "policy_strict.yaml")

    lenient_out = replay([tick_file], cfg=lenient, speed=0)
    strict_out = replay([tick_file], cfg=strict, speed=0)

    assert lenient_out and strict_out
    lenient_decisions = [r.decision for r in lenient_out]
    strict_decisions = [r.decision for r in strict_out]

    assert lenient_decisions != strict_decisions
    # ...and specifically because of the gate we moved, not some side effect.
    assert "REJECTED_INSUFFICIENT_REWARD_RISK" in strict_decisions
    assert "REJECTED_INSUFFICIENT_REWARD_RISK" not in lenient_decisions
