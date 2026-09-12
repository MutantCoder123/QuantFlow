"""Replay recorded ticks through the live decision pipeline (improved §5.4).

A threshold that has never been replayed is a guess. This module feeds a
recorded tick stream (journal/tick_recorder.py parquet) through exactly the
code the live system runs --

    RollingStateEngine.process_tick      (phantom candle / microstructure)
      -> RollingStateEngine._compute_symbol   (the flat feature payload)
      -> ReasoningEngine.build_structured_payload  (regime + math_setup)
      -> IntradayGatekeeper.evaluate           (admit / reject)

-- and returns one journal.feature_log.FeatureRecord per evaluated tick,
built by the same two helpers the live gatekeeper loop uses
(_numeric_features / _classify_decision), so a replayed record and a live
record are directly comparable.

Two properties make it useful, and both are bought by isolation rather than
by luck:

* **Determinism.** Every wall-clock read inside the pipeline is frozen to the
  tick's own recorded timestamp, and every process-wide accumulator the
  pipeline mutates is reset before the run. Two replays of the same file
  against the same policy are byte-identical.
* **Config sensitivity.** `cfg` is installed over ``core.policy_config``'s
  cached loader for the duration of the run, so every threshold read by the
  pipeline comes from the policy file under test rather than from
  config/policy_v1.yaml.

NOT safe to run concurrently with a live gatekeeper loop, or with another
replay(), in the same process: the pipeline keeps per-symbol state in
process-wide singletons (TerminalDashboard.active_states, the conviction /
regime registries, MicrostructureEngine's per-token accumulators,
RollingStateEngine's class-level dicts). replay() swaps those out for empty
ones and restores them in a ``finally``, which makes sequential runs
independent -- but a concurrent reader would see replay's state, not its own.
"""
from __future__ import annotations

import argparse
import contextlib
import logging
import time
from pathlib import Path

import pandas as pd
from freezegun import freeze_time

from journal.feature_log import FeatureRecord

logger = logging.getLogger(__name__)

# Bound before any freeze_time context exists. `speed` pacing is real elapsed
# wall-clock time (what a human watching the replay experiences), which is a
# different thing from the frozen clock the pipeline computes against --
# holding the reference here keeps the two independent no matter what a
# future freezegun decides to patch.
_REAL_SLEEP = time.sleep

# The frame process_tick appends completed phantom candles to. Starting empty
# is enough; process_tick's boundary-cross branch writes the first real row.
_LTF_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume", "oi"]

# Per-token accumulators MicrostructureEngine keeps at class level. They are
# only cleared when the IST session date changes, so replaying the same day
# twice would otherwise carry run 1's CVD/VWAP into run 2.
_MICRO_STATE = ("cvd_state", "vol_profile_state", "session_vwap_state",
                "whale_cvd_state", "whale_cvd_history", "last_vtt_state",
                "last_bba_state", "session_date")


def load_ticks(tick_files) -> pd.DataFrame:
    """Concatenate recorded tick parquets into one time-ordered frame.

    Ticks for one day are flushed across several files, so file order is not
    tick order -- sort by ts_ms. The sort is stable, so ticks sharing a
    millisecond keep their recorded order and the result is reproducible.
    """
    frames = [pd.read_parquet(Path(f)) for f in tick_files]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    return df.sort_values("ts_ms", kind="mergesort").reset_index(drop=True)


def _ist(ts_ms: int) -> pd.Timestamp:
    return pd.Timestamp(int(ts_ms), unit="ms", tz="UTC").tz_convert("Asia/Kolkata")


@contextlib.contextmanager
def _policy_override(cfg):
    """Make `cfg` the policy every call site sees for the duration of the run.

    Every reader (conviction_scorer, intraday_gatekeeper, ...) does a
    function-local ``from core.policy_config import load_policy``, which
    re-resolves the module attribute on each call -- so replacing the
    attribute is enough, no per-module patching needed. The real loader stays
    the cache owner; the replacement only supplies the default path.
    """
    import core.policy_config as policy_config

    original = policy_config.load_policy
    original.cache_clear()
    path = Path(cfg)

    def _scoped(p: Path | None = None):
        return original(path if p is None else p)

    policy_config.load_policy = _scoped
    try:
        yield original(path)
    finally:
        policy_config.load_policy = original
        original.cache_clear()


@contextlib.contextmanager
def _isolated(tokens: list[str]):
    """Give the run empty process-wide state, and hand back what was there.

    Entries are restored rather than merely cleared so that a replay invoked
    inside a longer-lived process (a test session, an operator shell) leaves
    the live registries pointing at the instances they had before, not at
    replay-polluted ones.
    """
    from conviction_scorer import ConvictionScorerRegistry
    from diagnostic_ui import TerminalDashboard
    from microstructure_engine import MicrostructureEngine
    from regime_manager import RegimeManagerRegistry
    from rolling_state_engine import RollingStateEngine

    saved_dashboard = (TerminalDashboard.active_states,
                       TerminalDashboard.catalyst_cache,
                       TerminalDashboard.global_market_context)
    saved_engine = (RollingStateEngine.live_options_state,
                    RollingStateEngine.daily_metrics_cache)
    saved_scorers = {t: ConvictionScorerRegistry._scorers.pop(t)
                     for t in tokens if t in ConvictionScorerRegistry._scorers}
    saved_managers = {t: RegimeManagerRegistry._managers.pop(t)
                      for t in tokens if t in RegimeManagerRegistry._managers}
    saved_micro = {}
    for name in _MICRO_STATE:
        d = getattr(MicrostructureEngine, name)
        saved_micro[name] = {t: d.pop(t) for t in tokens if t in d}

    TerminalDashboard.active_states = {}
    TerminalDashboard.catalyst_cache = {}
    TerminalDashboard.global_market_context = None
    RollingStateEngine.live_options_state = {}
    RollingStateEngine.daily_metrics_cache = {}
    try:
        yield
    finally:
        (TerminalDashboard.active_states, TerminalDashboard.catalyst_cache,
         TerminalDashboard.global_market_context) = saved_dashboard
        (RollingStateEngine.live_options_state,
         RollingStateEngine.daily_metrics_cache) = saved_engine
        for t in tokens:
            ConvictionScorerRegistry._scorers.pop(t, None)
            RegimeManagerRegistry._managers.pop(t, None)
        ConvictionScorerRegistry._scorers.update(saved_scorers)
        RegimeManagerRegistry._managers.update(saved_managers)
        for name in _MICRO_STATE:
            d = getattr(MicrostructureEngine, name)
            for t in tokens:
                d.pop(t, None)
            d.update(saved_micro[name])


def _build_engine(tokens: list[str]):
    """A RollingStateEngine bound to nothing but this replay.

    __init__ is bypassed (the house pattern in tests/) because it hydrates
    from data/cache_state.json, loads daily parquet metrics, and opens a
    TickRecorder -- live-session side effects that would make a replay depend
    on whatever the last live run left on disk, and would re-record the very
    ticks being read.
    """
    from rolling_state_engine import RollingStateEngine

    engine = RollingStateEngine.__new__(RollingStateEngine)
    engine.dfs = {t: {"ltf_df": pd.DataFrame(columns=_LTF_COLUMNS),
                      "htf_df": pd.DataFrame()} for t in tokens}
    engine.watchlist = {t: {"symbol": t.split("|")[-1]} for t in tokens}
    engine.phantom_candles = {}
    engine.last_tick_ts = {}
    engine._failures = {}
    engine.recorder = None
    engine._baselines, engine._baselines_mtime = {}, 0.0
    engine._flow_state, engine._flow_mtime = {}, 0.0
    return engine


def _step(engine, row, config_version: int) -> FeatureRecord | None:
    """One tick through the whole pipeline, yielding one FeatureRecord.

    Returns None while a token has no payload yet -- _compute_symbol is a
    no-op until that token's first 5-minute boundary has been crossed, which
    is the same warm-up the live system goes through at open.
    """
    from diagnostic_ui import TerminalDashboard
    from intraday_gatekeeper import IntradayGatekeeper
    from reasoning_engine import (ReasoningEngine, _classify_decision,
                                  _numeric_features)

    token = str(row.token)
    bids = [{"price": float(row.bid1), "quantity": int(row.bid_qty)}] if row.bid1 else None
    asks = [{"price": float(row.ask1), "quantity": int(row.ask_qty)}] if row.ask1 else None

    engine.process_tick(token=token, timestamp_ms=int(row.ts_ms), price=float(row.ltp),
                        volume=float(row.vtt), oi=float(row.oi), bids=bids, asks=asks)
    # Live, this runs on a 1.5s wall-clock timer decoupled from tick arrival.
    # Driving it per tick instead is what makes replay a function of the tape
    # alone rather than of how fast the replaying machine happens to be.
    engine._compute_symbol(token, engine.phantom_candles.get(token))

    payload = TerminalDashboard.active_states.get(token)
    if not payload:
        return None

    # advance_state=True: replay is the single authoritative reconstruction of
    # history for its own isolated run, exactly as the live gatekeeper loop is
    # the one caller allowed to advance whipsaw/regime state (Task 2.2).
    structured = ReasoningEngine.build_structured_payload(
        token, payload, {}, advance_state=True)
    gatekeeper_res = IntradayGatekeeper.evaluate(
        structured_payload=structured, raw_payload=payload,
        user_context={}, ltp=payload.get("ltp", 0.0))

    math_setup = structured.get("math_setup") or {}
    regime_meta = structured.get("market_regime") or {}
    return FeatureRecord(
        # The tick's own recorded time, never time.time(): two replays run at
        # different moments must still produce identical records.
        ts=int(row.ts_ms) // 1000,
        symbol=ReasoningEngine._normalize_symbol(payload.get("symbol") or token),
        config_version=config_version,
        features=_numeric_features(payload),
        staleness={"microstructure": float(payload.get("data_age_s", 0.0))},
        regime=regime_meta.get("current_regime", "UNKNOWN"),
        session_phase=regime_meta.get("session_phase", "UNKNOWN"),
        composite=math_setup.get("composite_score"),
        decision=_classify_decision(math_setup, gatekeeper_res),
    )


def replay(tick_files: list[Path], cfg: Path | str, speed: float = 0) -> list[FeatureRecord]:
    """Feed recorded ticks through the live pipeline under an isolated,
    deterministic run.

    `cfg` is a policy YAML path, installed over core.policy_config for the
    duration of the call. `speed` paces replay: 0 = as fast as possible (no
    sleeps); >0 = a multiplier on real elapsed inter-tick time (1.0 =
    real-time, 10.0 = 10x real-time). Not safe to call concurrently with a
    live gatekeeper loop or another replay() in the same process -- see the
    module docstring for why.
    """
    ticks = load_ticks(tick_files)
    if ticks.empty:
        return []

    tokens = sorted({str(t) for t in ticks["token"]})
    records: list[FeatureRecord] = []

    with _policy_override(cfg) as policy, _isolated(tokens):
        engine = _build_engine(tokens)
        prev_ms = None
        # One freeze for the whole loop, advanced per tick: every time.time()
        # / datetime.now() inside the pipeline then reports the tick's own
        # timestamp, so data_age_s, session phase, the market-hours guard and
        # the entry cutoff are all functions of the tape.
        with freeze_time(_ist(int(ticks["ts_ms"].iloc[0]))) as frozen:
            for row in ticks.itertuples(index=False):
                ts_ms = int(row.ts_ms)
                if speed > 0 and prev_ms is not None:
                    _REAL_SLEEP(max(0.0, (ts_ms - prev_ms) / 1000.0 / speed))
                prev_ms = ts_ms
                frozen.move_to(_ist(ts_ms))
                try:
                    record = _step(engine, row, policy.version)
                except Exception as e:
                    # Per-tick isolation, mirroring run_one_cycle: one bad
                    # symbol must not truncate the rest of the tape.
                    logger.error(f"replay step failed at ts_ms={ts_ms}: {e}", exc_info=True)
                    continue
                if record is not None:
                    records.append(record)

    return records


def main(argv=None) -> int:
    from paths import BASE_DIR, REPO_ROOT, TICKS_DIR

    parser = argparse.ArgumentParser(
        prog="python -m replay.runner",
        description="Replay a recorded tick day through the decision pipeline.")
    parser.add_argument("--date", required=True,
                        help="session date, YYYY-MM-DD (reads data/ticks/date=<DATE>/)")
    parser.add_argument("--config", required=True,
                        help="policy YAML path; a relative path is tried as given, then "
                             "against the repo root, then against trading_copilot/")
    parser.add_argument("--speed", type=float, default=0,
                        help="0 = as fast as possible; >0 = multiplier on real time")
    args = parser.parse_args(argv)

    partition = TICKS_DIR / f"date={args.date}"
    files = sorted(partition.glob("ticks_*.parquet"))
    if not files:
        print(f"No tick files under {partition}")
        return 1

    # `config/policy_v2.yaml` reads naturally from either the repo root or
    # trading_copilot/ (where CONFIG_DIR actually lives), so accept both.
    cfg = Path(args.config)
    if not cfg.is_absolute() and not cfg.is_file():
        cfg = next((base / cfg for base in (REPO_ROOT, BASE_DIR)
                    if (base / cfg).is_file()), cfg)
    if not cfg.is_file():
        print(f"No policy config at {cfg}")
        return 1

    records = replay(files, cfg, speed=args.speed)

    counts: dict[str, int] = {}
    for r in records:
        counts[r.decision] = counts.get(r.decision, 0) + 1
    print(f"{len(files)} file(s), {len(records)} record(s) from {partition}")
    for decision, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {n:>7}  {decision}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
