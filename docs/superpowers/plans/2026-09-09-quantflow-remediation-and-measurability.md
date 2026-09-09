# QuantFlow Remediation & Measurability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn QuantFlow from a system whose 102 unfitted thresholds can never be validated into one that records its own evidence, computes decisions through pure replayable functions, sizes positions against real risk, and reports only confidence it has measured.

**Architecture:** Six sequential phases. Phase 0 fixes correctness defects so that everything recorded afterwards is trustworthy. Phase 1 adds append-only tick and feature-vector recording as a pure addition (no behaviour change) so the dataset starts accumulating immediately. Phase 2 extracts pure `build_features`/`evaluate` functions driven by external YAML config and moves ingestion behind a single-writer queue, which makes replay and unit testing possible. Phases 3–5 add the risk layer, the measurement harness, and operator-facing surfaces on top of that foundation.

**Tech Stack:** Python 3.12.10, pandas 3.0.3 (copy-on-write default), numpy 2.2.6, pyarrow 24.0.0, FastAPI, pytest, PyYAML, freezegun. Existing: pandas_ta, TA-Lib 0.6.8, upstox_client, google-genai 2.6.0.

**Spec:** [improved_architecture_and_features.md](../../../improved_architecture_and_features.md) (target design) and [drawbacks_false_claimed.md](../../../drawbacks_false_claimed.md) (defects to retire). Architecture reference: [Architecture_detailed_overview.md](../../../Architecture_detailed_overview.md).

## Global Constraints

- **Python 3.12.10**; **pandas 3.0.3** — copy-on-write is default. Never rely on chained assignment (`df[a][b] = x`); use `.loc`. `inplace=True` on a slice is a no-op.
- **Import style:** existing modules use flat imports (`from technical_engine import MathEngine`) and rely on `trading_copilot/` being on `sys.path`. New modules under `trading_copilot/core/` and `trading_copilot/journal/` use package-relative imports. Both must work — `tests/conftest.py` puts both the repo root and `trading_copilot/` on `sys.path`.
- **All filesystem paths** come from `trading_copilot/paths.py` after Task 0.2. No new `os.path.dirname(__file__)` chains, ever.
- **Timezone:** all market logic uses `ZoneInfo("Asia/Kolkata")`. Never `datetime.utcnow()` (deprecated in 3.12) and never a hand-added 5:30 offset.
- **Market session:** 09:15–15:30 IST, Mon–Fri. Forced square-off 15:20 IST.
- **Never fabricate data.** If a value is unknown, propagate `None` and render it as unknown. This is the rule Phase 0 Task 0.6 and Phase 4 Task 4.2 both enforce.
- **Every task ends with a passing test run and a commit.** No task is complete without both.
- **Branch:** all work on `feat/measurability`, created in Task 0.1. Do not commit to `master`.
- **Do not reformat untouched code.** Diffs stay reviewable.

---

## File Structure

**New files:**

| Path | Responsibility |
|---|---|
| `trading_copilot/paths.py` | Single source of truth for every filesystem path |
| `trading_copilot/config/policy_v1.yaml` | All 102 decision thresholds, versioned |
| `trading_copilot/config/costs.yaml` | Brokerage/STT/GST rate card |
| `trading_copilot/config/clusters.yaml` | Correlation groups for exposure limits |
| `trading_copilot/config/risk.yaml` | Capital, per-trade risk, daily loss limit |
| `trading_copilot/core/types.py` | `FeatureVector`, `Proposal`, `Rejection`, `SizedProposal` |
| `trading_copilot/core/policy_config.py` | Load + validate YAML into `PolicyConfig` |
| `trading_copilot/core/costs.py` | Round-trip cost model |
| `trading_copilot/core/risk.py` | L3 position sizing and exposure limits |
| `trading_copilot/journal/tick_recorder.py` | L0 append-only tick capture |
| `trading_copilot/journal/feature_log.py` | L5 append-only feature-vector capture |
| `trading_copilot/journal/outcome_labeller.py` | Bar-accurate outcome resolution |
| `trading_copilot/replay/runner.py` | Replay harness over recorded ticks |
| `tests/conftest.py` | `sys.path` setup + shared fixtures |
| `tests/test_*.py` | One test module per unit above |

**Modified files:** `rolling_state_engine.py`, `microstructure_engine.py`, `signal_ledger.py`, `performance_analyzer.py`, `conviction_scorer.py`, `intraday_gatekeeper.py`, `reasoning_engine.py`, `semantic_tagger.py`, `api_server.py`, `derivatives_worker.py`, `data_services/upstox_feed.py`, `templates/index.html`, `requirements.txt`, `README.md`, `.env.example`.

---

# PHASE 0 — Foundation & P0 Correctness

**Why first:** every byte Phase 1 records inherits this data quality. Recording corrupt volume for a month produces a corrupt dataset that cannot be repaired retroactively.

**Exit criteria:** `pytest` green; no fabricated values reach the UI; volume accounting correct; all paths resolve.

---

### Task 0.1: Test infrastructure and branch

**Files:**
- Create: `tests/conftest.py`, `pytest.ini`
- Modify: `requirements.txt` (currently corrupt — see Step 3)

**Interfaces:**
- Produces: a working `pytest` invocation from the repo root; `trading_copilot/` importable in tests.

- [ ] **Step 1: Create the branch**

```bash
git checkout -b feat/measurability
```

- [ ] **Step 2: Install test dependencies**

```bash
./.venv/Scripts/python.exe -m pip install pytest pytest-asyncio pyyaml freezegun
```

- [ ] **Step 3: Repair `requirements.txt`**

The file is corrupt: the last two entries were appended as UTF-16LE bytes into a UTF-8 file, so
`pip install -r requirements.txt` produces garbage package names. Verify:

```bash
./.venv/Scripts/python.exe -c "print(repr(open('requirements.txt','rb').read()[-60:]))"
```

Expected to show `u\x00p\x00s\x00t\x00o\x00x...`. Rewrite the whole file as clean UTF-8:

```text
aiohttp
fastapi
google-genai
numpy
pandas
pandas-ta
pyarrow
pyotp
python-dotenv
pytz
scipy
TA-Lib
upstox-python
uvicorn
websockets
# test + config
pytest
pytest-asyncio
PyYAML
freezegun
```

`smartapi-python`, `logzero`, `websocket-client`, `rich` and `playwright` are dropped: they belong to the
dead Angel One stack and the disabled headless-login path
([drawbacks §F](../../../drawbacks_false_claimed.md#f--dead-code)).

- [ ] **Step 4: Create `pytest.ini`**

```ini
[pytest]
testpaths = tests
python_files = test_*.py
addopts = -q --strict-markers
markers =
    slow: tests that take more than a second
```

- [ ] **Step 5: Create `tests/conftest.py`**

```python
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COPILOT = ROOT / "trading_copilot"

for _p in (str(ROOT), str(COPILOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
```

- [ ] **Step 6: Verify the harness runs**

Create `tests/test_smoke.py`:

```python
def test_imports_resolve():
    import technical_engine
    import microstructure_engine
    assert hasattr(technical_engine, "MathEngine")
```

Run: `./.venv/Scripts/python.exe -m pytest tests/test_smoke.py -v`
Expected: PASS (1 passed)

- [ ] **Step 7: Commit**

```bash
git add tests/ pytest.ini requirements.txt
git commit -m "test: add pytest harness and repair corrupt requirements.txt"
```

---

### Task 0.2: Centralise path resolution

**Files:**
- Create: `trading_copilot/paths.py`, `tests/test_paths.py`
- Modify: `trading_copilot/rolling_state_engine.py:31,117,316`, `trading_copilot/derivatives_worker.py:16-17,63,221`, `trading_copilot/signal_ledger.py:17,166`, `trading_copilot/history_manager.py:13`, `trading_copilot/api_server.py:29,433`, `trading_copilot/reasoning_engine.py:651`, `trading_copilot/warm_layer_engine.py:10-12`, `trading_copilot/data_services/parquet_engine.py:14-16`, `trading_copilot/data_services/macro_worker.py:11-12`

**Interfaces:**
- Produces: `paths.BASE_DIR`, `paths.DATA_DIR`, `paths.SIGNALS_DIR`, `paths.TICKS_DIR`, `paths.FEATURES_DIR`, `paths.CONFIG_DIR`, `paths.WATCHLIST_PATH`, `paths.TOKEN_PATH`, `paths.PLAYBOOK_PATH`, `paths.MACRO_BASELINES_PATH`, `paths.INSTITUTIONAL_FLOW_PATH`, `paths.CACHE_STATE_PATH`, `paths.TRADE_HISTORY_PATH`, `paths.ensure_dirs() -> None`

Fixes [§A-9](../../../drawbacks_false_claimed.md#-a-9-two-data-paths-resolve-to-a-directory-that-does-not-exist): `rolling_state_engine.py:117` and `derivatives_worker.py:16-17` both resolve to `AlgoTrade/data`, which does not exist.

- [ ] **Step 1: Write the failing test**

`tests/test_paths.py`:

```python
from pathlib import Path
import paths


def test_data_dir_is_inside_trading_copilot():
    assert paths.DATA_DIR.parent.name == "trading_copilot"
    assert paths.DATA_DIR.is_dir(), f"{paths.DATA_DIR} does not exist"


def test_known_data_files_resolve():
    assert paths.MACRO_BASELINES_PATH.is_file()
    assert list(paths.DATA_DIR.glob("*_1D.parquet")), "no parquet files found"


def test_ensure_dirs_is_idempotent():
    paths.ensure_dirs()
    paths.ensure_dirs()
    for d in (paths.DATA_DIR, paths.SIGNALS_DIR, paths.TICKS_DIR, paths.FEATURES_DIR):
        assert d.is_dir()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_paths.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'paths'`

- [ ] **Step 3: Create `trading_copilot/paths.py`**

```python
"""Single source of truth for every filesystem path in the project.

Never compute a path with os.path.dirname(__file__) chains again — two such
chains previously resolved to a directory that does not exist, silently
disabling parquet metric loading and IV-Rank history.
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent            # .../AlgoTrade/trading_copilot
REPO_ROOT = BASE_DIR.parent                            # .../AlgoTrade

DATA_DIR = BASE_DIR / "data"
SIGNALS_DIR = DATA_DIR / "signals"
TICKS_DIR = DATA_DIR / "ticks"
FEATURES_DIR = DATA_DIR / "features"
CACHE_DIR = BASE_DIR / "cache"
CONFIG_DIR = BASE_DIR / "config"

WATCHLIST_PATH = BASE_DIR / "watchlist.csv"
TOKEN_PATH = BASE_DIR / "upstox_token.json"
PLAYBOOK_PATH = BASE_DIR / "playbook_state.json"

MACRO_BASELINES_PATH = DATA_DIR / "macro_baselines.json"
INSTITUTIONAL_FLOW_PATH = DATA_DIR / "institutional_flow.json"
CACHE_STATE_PATH = DATA_DIR / "cache_state.json"
TRADE_HISTORY_PATH = DATA_DIR / "trade_history.json"

_WRITABLE = (DATA_DIR, SIGNALS_DIR, TICKS_DIR, FEATURES_DIR, CACHE_DIR, CONFIG_DIR)


def ensure_dirs() -> None:
    """Create every directory the system writes to. Safe to call repeatedly."""
    for d in _WRITABLE:
        d.mkdir(parents=True, exist_ok=True)


def parquet_path(symbol: str) -> Path:
    return DATA_DIR / f"{symbol}_1D.parquet"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_paths.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Migrate every call site**

Replace each listed path expression with the `paths` constant. Examples:

```python
# rolling_state_engine.py:31 — was:
#   self.cache_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'cache_state.json')
from paths import CACHE_STATE_PATH, DATA_DIR, MACRO_BASELINES_PATH
self.cache_file = CACHE_STATE_PATH

# rolling_state_engine.py:117 — was the BROKEN dirname(dirname(...)) chain:
data_dir = DATA_DIR

# rolling_state_engine.py:316 — was a per-symbol per-cycle disk read:
baselines_path = MACRO_BASELINES_PATH

# derivatives_worker.py:16-17 — was the BROKEN chain:
from paths import DATA_DIR, WATCHLIST_PATH
# and :221 — was os.path.join(_BASE_DIR, 'trading_copilot', 'watchlist.csv')
watchlist_file = WATCHLIST_PATH

# api_server.py:433 and reasoning_engine.py:651 — were CWD-relative literals:
from paths import PLAYBOOK_PATH
playbook_path = PLAYBOOK_PATH
```

Also replace the CWD-relative debug dump at `derivatives_worker.py:279-281`:

```python
from paths import CACHE_DIR
debug_path = CACHE_DIR / "derivatives_debug.json"
with open(debug_path, "w") as f:
    json.dump(debug_dump, f, indent=2)
```

- [ ] **Step 6: Verify no stale path chains remain**

```bash
grep -rn "dirname(os.path.dirname" trading_copilot/ --include=*.py | grep -v __pycache__
```
Expected: no output (or only `paths.py` itself, which uses `Path`, not this idiom).

- [ ] **Step 7: Verify parquet metrics now actually load**

```bash
cd trading_copilot && ../.venv/Scripts/python.exe -c "
from paths import DATA_DIR, parquet_path
print('DATA_DIR exists:', DATA_DIR.is_dir())
print('RELIANCE parquet:', parquet_path('RELIANCE').is_file())
"
```
Expected: both `True`.

- [ ] **Step 8: Commit**

```bash
git add trading_copilot/paths.py tests/test_paths.py trading_copilot/
git commit -m "fix: centralise path resolution, repairing two dead data paths (A-9)"
```

---

### Task 0.3: Correct VTT volume accounting

**Files:**
- Modify: `trading_copilot/microstructure_engine.py:133-208`, `trading_copilot/rolling_state_engine.py:141-239`
- Test: `tests/test_volume_accounting.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `MicrostructureEngine.generate_microstructure_payload()` return dict gains key `"tick_volume": float`. `RollingStateEngine.process_tick()` signature unchanged, but `phantom['volume']` now accumulates deltas.

Fixes [§A-2](../../../drawbacks_false_claimed.md#-a-2-phantom-candle-volume-accumulates-a-cumulative-counter): `phantom['volume'] += volume` adds the *cumulative* `vtt` on every tick, and the corrupted phantom is then committed into `ltf_df` every 5 minutes, permanently poisoning the volume baseline.

Also fixes the anomaly-guard defect from [§A-3](../../../drawbacks_false_claimed.md#-a-3-session-state-is-reset-inconsistently-and-the-vtt-anomaly-guard-zeroes-real-volume): `tick_vol > prev_vol * 0.5` discards legitimate opening-range volume.

- [ ] **Step 1: Write the failing tests**

`tests/test_volume_accounting.py`:

```python
import pytest
from microstructure_engine import MicrostructureEngine as M

TOKEN = "NSE_EQ|TESTSYM"


@pytest.fixture(autouse=True)
def clean_state():
    for d in (M.cvd_state, M.vol_profile_state, M.session_vwap_state,
              M.whale_cvd_state, M.whale_cvd_history):
        d.clear()
    for attr in ("last_vtt_state", "last_bba_state"):
        if hasattr(M, attr):
            getattr(M, attr).clear()
    yield


def _tick(vtt, price=100.0):
    return M.generate_microstructure_payload({
        "token": TOKEN, "price": price, "volume": vtt,
        "bids": [{"quantity": 10, "price": price - 0.05}],
        "asks": [{"quantity": 10, "price": price + 0.05}],
    })


def test_first_tick_establishes_baseline_with_zero_volume():
    assert _tick(1_000_000.0)["tick_volume"] == 0.0


def test_tick_volume_is_the_delta():
    _tick(1_000_000.0)
    assert _tick(1_002_500.0)["tick_volume"] == 2_500.0
    assert _tick(1_003_000.0)["tick_volume"] == 500.0


def test_large_opening_range_increment_is_not_discarded():
    """Regression: the old guard zeroed any increment > 50% of cumulative,
    which is routine in the first 30 minutes."""
    _tick(1_000_000.0)
    assert _tick(3_000_000.0)["tick_volume"] == 2_000_000.0


def test_counter_going_backwards_yields_zero_not_negative():
    """A reconnect or session rollover resets vtt; never emit negative volume."""
    _tick(5_000_000.0)
    assert _tick(1_000.0)["tick_volume"] == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_volume_accounting.py -v`
Expected: `test_first_tick...` and `test_tick_volume_is_the_delta` FAIL with `KeyError: 'tick_volume'`;
`test_large_opening_range_increment_is_not_discarded` FAIL (returns 0.0 due to the guard).

- [ ] **Step 3: Fix `microstructure_engine.py`**

Replace lines 139–151 with:

```python
        if not hasattr(cls, 'last_vtt_state'):
            cls.last_vtt_state = {}

        prev_vol = cls.last_vtt_state.get(token)
        if prev_vol is None or vol < prev_vol:
            # First tick of the session, or the counter reset (reconnect/rollover).
            # Establish a baseline; emit no volume for this tick.
            tick_vol = 0.0
        else:
            tick_vol = float(vol - prev_vol)

        cls.last_vtt_state[token] = vol
```

Then add `tick_volume` to the return dict at the end of `generate_microstructure_payload`:

```python
        return {
            "obi": round(obi, 4),
            "cvd": cvd,
            "tick_volume": tick_vol,
            "poc_price": poc,
            "poc_distance_pct": round(poc_distance_pct, 4),
            "session_vwap": session_vwap,
            "price_to_vwap_pct": round(price_to_vwap_pct, 4),
            "whale_cvd_live": whale_cvd,
            "whale_cvd_ema_1h": whale_ema,
            "whale_cvd_slope": whale_slope
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_volume_accounting.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Write the failing phantom-candle test**

Append to `tests/test_volume_accounting.py`:

```python
import pandas as pd
from rolling_state_engine import RollingStateEngine


def _engine():
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-09-09 09:15:00"]),
        "open": [100.0], "high": [100.0], "low": [100.0],
        "close": [100.0], "volume": [50_000.0], "oi": [0.0],
    })
    eng = RollingStateEngine.__new__(RollingStateEngine)   # bypass cache hydration
    eng.dfs = {TOKEN: {"ltf_df": df, "htf_df": pd.DataFrame()}}
    eng.watchlist = {TOKEN: {"symbol": "TESTSYM"}}
    eng.phantom_candles = {}
    return eng


def test_phantom_volume_accumulates_deltas_not_cumulative_total():
    eng = _engine()
    base_ms = int(pd.Timestamp("2026-09-09 09:20:00").timestamp() * 1000)
    for offset, vtt in ((0, 1_000_000.0), (1_000, 1_002_500.0), (2_000, 1_003_000.0)):
        eng.process_tick(token=TOKEN, timestamp_ms=base_ms + offset,
                         price=100.0, volume=vtt, oi=0.0,
                         bids=[{"quantity": 10, "price": 99.95}],
                         asks=[{"quantity": 10, "price": 100.05}])
    # deltas: 0 (baseline) + 2500 + 500
    assert eng.phantom_candles[TOKEN]["volume"] == 3_000.0
```

- [ ] **Step 6: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_volume_accounting.py::test_phantom_volume_accumulates_deltas_not_cumulative_total -v`
Expected: FAIL — asserts ~3,005,500.0 (the cumulative total summed three times).

- [ ] **Step 7: Fix `rolling_state_engine.process_tick`**

Restructure so microstructure is computed **before** the phantom update, and the phantom consumes the
delta. Replace the body from line 185 (`boundary_ts = ...`) through line 239 with:

```python
        boundary_ts = tick_ts.floor('5min')

        # Compute microstructure FIRST — it owns VTT differencing.
        from microstructure_engine import MicrostructureEngine
        prev_phantom = self.phantom_candles.get(token)
        prev_micro = prev_phantom.get('microstructure', {}) if prev_phantom else {}

        micro_state = MicrostructureEngine.generate_microstructure_payload({
            'token': token, 'price': price, 'volume': volume,
            'bids': bids, 'asks': asks,
        })
        if not bids or not asks:
            # Carry the last known OBI when this tick had no depth payload.
            micro_state['obi'] = prev_micro.get('obi', 0.0)

        tick_volume = micro_state['tick_volume']

        phantom = prev_phantom
        if not phantom or phantom['timestamp'] < boundary_ts:
            if phantom:
                target_df = self.dfs[token].get('ltf_df')
                if target_df is not None:
                    target_df.loc[len(target_df)] = phantom

            phantom = {
                'timestamp': boundary_ts,
                'open': price, 'high': price, 'low': price, 'close': price,
                'volume': tick_volume,
                'oi': oi,
                'microstructure': micro_state,
            }
            self.phantom_candles[token] = phantom
        else:
            phantom['high'] = max(phantom['high'], price)
            phantom['low'] = min(phantom['low'], price)
            phantom['close'] = price
            phantom['volume'] += tick_volume
            phantom['oi'] = oi
            phantom['microstructure'] = micro_state
```

Note: `target_df.loc[len(target_df)] = phantom` silently drops the extra `microstructure` key under
pandas 3.0.3 (verified), which is the desired behaviour — `ltf_df` keeps only OHLCV+oi columns.

- [ ] **Step 8: Run the full test file**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_volume_accounting.py -v`
Expected: PASS (5 passed)

- [ ] **Step 9: Commit**

```bash
git add trading_copilot/microstructure_engine.py trading_copilot/rolling_state_engine.py tests/test_volume_accounting.py
git commit -m "fix: phantom candle accumulates VTT deltas, not cumulative totals (A-2, A-3)"
```

---

### Task 0.4: Single session-boundary reset

**Files:**
- Modify: `trading_copilot/microstructure_engine.py:58-82,132-152`
- Test: `tests/test_session_reset.py`

**Interfaces:**
- Produces: `MicrostructureEngine.roll_session_if_needed(token: str, session_date: str) -> bool` (returns `True` when a reset occurred).

Fixes [§A-3 Finding 2](../../../drawbacks_false_claimed.md#-a-3-session-state-is-reset-inconsistently-and-the-vtt-anomaly-guard-zeroes-real-volume): four of seven per-token dicts miss the daily reset, so POC accumulates across days and whale-CVD history carries the previous session into today's EMA.

- [ ] **Step 1: Write the failing test**

`tests/test_session_reset.py`:

```python
import pytest
from freezegun import freeze_time
from microstructure_engine import MicrostructureEngine as M

TOKEN = "NSE_EQ|TESTSYM"
ALL_STATE = ("cvd_state", "vol_profile_state", "session_vwap_state",
             "whale_cvd_state", "whale_cvd_history", "last_vtt_state",
             "last_bba_state")


@pytest.fixture(autouse=True)
def clean_state():
    for name in ALL_STATE:
        if hasattr(M, name):
            getattr(M, name).clear()
    if hasattr(M, "session_date"):
        M.session_date.clear()
    yield


def _tick(vtt, price=100.0):
    return M.generate_microstructure_payload({
        "token": TOKEN, "price": price, "volume": vtt,
        "bids": [{"quantity": 10, "price": price - 0.05}],
        "asks": [{"quantity": 10, "price": price + 0.05}],
    })


def test_every_per_token_dict_is_cleared_on_new_session():
    with freeze_time("2026-09-09 10:00:00+05:30"):
        _tick(1_000_000.0); _tick(1_500_000.0, price=101.0)
        assert M.vol_profile_state[TOKEN]
        assert TOKEN in M.last_vtt_state

    with freeze_time("2026-09-10 09:16:00+05:30"):
        _tick(2_000.0)
        # POC must reflect only today
        assert len(M.vol_profile_state[TOKEN]) == 1
        assert M.cvd_state.get(TOKEN, 0) == 0
        assert len(M.whale_cvd_history.get(TOKEN, [])) <= 1


def test_poc_does_not_carry_yesterdays_prices():
    with freeze_time("2026-09-09 10:00:00+05:30"):
        _tick(1_000_000.0, price=500.0); _tick(1_100_000.0, price=500.0)
    with freeze_time("2026-09-10 09:16:00+05:30"):
        _tick(1_000.0, price=100.0); _tick(5_000.0, price=100.0)
        assert M.calculate_poc(TOKEN, 100.0) == 100
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_session_reset.py -v`
Expected: FAIL — `test_poc_does_not_carry_yesterdays_prices` returns 500 (yesterday's bin retained).

- [ ] **Step 3: Add the reset helper**

In `microstructure_engine.py`, add a `session_date` class dict and the helper:

```python
class MicrostructureEngine:
    cvd_state = {}
    vol_profile_state = {}
    session_vwap_state = {}
    whale_cvd_state = {}
    whale_cvd_history = {}
    last_vtt_state = {}
    last_bba_state = {}
    session_date = {}

    @classmethod
    def _ist_session_date(cls) -> str:
        import datetime
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d")

    @classmethod
    def roll_session_if_needed(cls, token: str, session_date: str | None = None) -> bool:
        """Clear EVERY per-token accumulator when the IST trading date changes.

        One place, all dicts — so a new accumulator added later cannot be
        forgotten the way vol_profile_state and whale_cvd_history were.
        """
        today = session_date or cls._ist_session_date()
        if cls.session_date.get(token) == today:
            return False
        cls.session_date[token] = today
        for d in (cls.cvd_state, cls.vol_profile_state, cls.session_vwap_state,
                  cls.whale_cvd_state, cls.whale_cvd_history,
                  cls.last_vtt_state, cls.last_bba_state):
            d.pop(token, None)
        return True
```

- [ ] **Step 4: Call it once, at the top of the payload builder**

At the start of `generate_microstructure_payload`, immediately after `token` is read:

```python
        token = tick_dict.get('token')
        cls.roll_session_if_needed(token)
```

- [ ] **Step 5: Remove the now-duplicated reset from `update_session_vwap`**

Delete lines 68–75 (the `if state['last_reset_date'] != current_date_str:` block and its `pop` calls);
`update_session_vwap` keeps only accumulation. Retain `last_reset_date` in the dict for diagnostics.

- [ ] **Step 6: Run both test files**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_session_reset.py tests/test_volume_accounting.py -v`
Expected: PASS (7 passed)

- [ ] **Step 7: Commit**

```bash
git add trading_copilot/microstructure_engine.py tests/test_session_reset.py
git commit -m "fix: single session-boundary reset covering all per-token state (A-3)"
```

---

### Task 0.5: Fix SignalLedger crash on held positions

**Files:**
- Modify: `trading_copilot/signal_ledger.py:53-55`
- Test: `tests/test_signal_ledger.py`

Fixes [§A-8](../../../drawbacks_false_claimed.md#-a-8-signalledgerrecord_signal-raises-whenever-a-position-is-held): `build_structured_payload` sets `execution_geometry = None` when a position is held, and `.get(key, default)` returns the stored `None`, so `None.get("padded_stop")` raises. Position-management signals are therefore never recorded.

- [ ] **Step 1: Write the failing test**

`tests/test_signal_ledger.py`:

```python
import pytest
from signal_ledger import SignalLedger


@pytest.fixture(autouse=True)
def clear_pending():
    SignalLedger._pending_signals.clear()
    yield
    SignalLedger._pending_signals.clear()


def test_record_signal_survives_null_geometry(tmp_path, monkeypatch):
    """When a position is held, execution_geometry and expectancy_matrix are
    explicitly None — recording must not raise."""
    monkeypatch.setattr(SignalLedger, "_append_to_log", classmethod(lambda cls, r, d=None: None))

    SignalLedger.record_signal(
        symbol="SAIL",
        execution_ticket={"verdict": "CONFIRM", "action_directive": "CLOSE_EXISTING"},
        math_setup={"composite_score": 0.31,
                    "execution_geometry": None,
                    "expectancy_matrix": None},
        market_regime={"current_regime": "TREND_EXPANSION", "session_phase": "POWER_HOUR"},
        ltp=132.5,
    )

    assert len(SignalLedger._pending_signals) == 1
    snap = next(iter(SignalLedger._pending_signals.values()))["record"]["signal_snapshot"]
    assert snap["padded_stop"] == 0.0
    assert snap["implied_probability"] is None or snap["implied_probability"] == 0.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_signal_ledger.py -v`
Expected: FAIL with `AttributeError: 'NoneType' object has no attribute 'get'`

- [ ] **Step 3: Fix `record_signal`**

Replace lines 44–56 with:

```python
        geo = math_setup.get("execution_geometry") or {}
        exp = math_setup.get("expectancy_matrix") or {}

        record = {
            "signal_id": signal_id,
            "symbol": symbol,
            "timestamp": ts,
            "datetime_ist": now.isoformat(),
            "session_date": date_str,
            "signal_snapshot": {
                "ltp_at_signal": ltp,
                "regime": market_regime.get("current_regime", "UNKNOWN"),
                "session_phase": market_regime.get("session_phase", "UNKNOWN"),
                "composite_score": math_setup.get("composite_score", 0.0),
                "implied_probability": exp.get("implied_probability"),
                "verdict": execution_ticket.get("verdict", "UNKNOWN"),
                "action_directive": execution_ticket.get("action_directive", "UNKNOWN"),
                "bias": bias,
                "padded_stop": geo.get("padded_stop", 0.0),
                "calculated_target": geo.get("calculated_target", 0.0),
                "calculated_entry": geo.get("calculated_entry", 0.0),
            },
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_signal_ledger.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add trading_copilot/signal_ledger.py tests/test_signal_ledger.py
git commit -m "fix: record_signal no longer raises on null geometry (A-8)"
```

---

### Task 0.6: Remove the fabricated macro narrative

**Files:**
- Modify: `trading_copilot/templates/index.html:641-648`

Fixes [§A-1](../../../drawbacks_false_claimed.md#-a-1-the-ui-fabricates-a-bullish-macro-narrative-and-presents-it-as-live-analysis) — the single most dangerous defect: a hardcoded bullish macro thesis rendered with a current timestamp whenever the real one is absent, which includes every session's first 30 minutes.

- [ ] **Step 1: Delete the mock block**

Remove lines 641–648 entirely (the `// MOCK DATA FALLBACK FOR GLOBAL MARKET CONTEXT` block).

- [ ] **Step 2: Render an explicit unknown state**

Replace the `if (payload.global_market_context) { ... }` block that follows with:

```js
                // Macro context: render truthfully, never fabricate.
                {
                    const ctx = payload.global_market_context;
                    const badge = document.getElementById('macro-sentiment-badge');
                    const summary = document.getElementById('macro-summary');
                    const timeElem = document.getElementById('macro-time');

                    if (!ctx) {
                        if (summary) summary.textContent =
                            'Macro context unavailable — news_feed (:8003) has not reported yet.';
                        if (badge) {
                            badge.textContent = 'UNAVAILABLE';
                            badge.className = 'px-2 py-0.5 text-[10px] font-bold rounded border ' +
                                              'bg-slate-800 text-slate-500 border-slate-700';
                        }
                        if (timeElem) timeElem.textContent = '';
                    } else {
                        if (summary) summary.textContent = ctx.summary || 'No summary available.';
                        if (badge) badge.textContent = ctx.sentiment || 'NEUTRAL';
                        if (ctx.timestamp && timeElem) {
                            timeElem.textContent = 'Updated: ' +
                                new Date(ctx.timestamp * 1000).toLocaleTimeString();
                        }
                        let bg = 'bg-slate-800', tx = 'text-slate-400', br = 'border-slate-700';
                        if (ctx.sentiment === 'BULLISH') {
                            bg = 'bg-emerald-500/20'; tx = 'text-emerald-400'; br = 'border-emerald-500/30';
                        } else if (ctx.sentiment === 'BEARISH') {
                            bg = 'bg-rose-500/20'; tx = 'text-rose-400'; br = 'border-rose-500/30';
                        } else if (ctx.sentiment === 'MIXED') {
                            bg = 'bg-purple-500/20'; tx = 'text-purple-400'; br = 'border-purple-500/30';
                        }
                        if (badge) badge.className =
                            `px-2 py-0.5 text-[10px] font-bold rounded border ${bg} ${tx} ${br}`;
                    }
                }
```

Note `textContent` rather than `innerText`/`innerHTML` — this also closes the
[§E-4](../../../drawbacks_false_claimed.md#e--security-and-operations) XSS sink on LLM/news-derived text.

- [ ] **Step 2b: Verify no other fabricated fallback survives**

```bash
grep -n -i "mock\|fabricat\|hardcode" trading_copilot/templates/index.html | grep -vi placeholder
```
Expected: no output.

- [ ] **Step 3: Manual verification**

Start only `main.py` (leave `news_feed.py` stopped), open `http://localhost:8000`.
Expected: the macro panel reads `UNAVAILABLE` with no timestamp and no narrative text.

- [ ] **Step 4: Commit**

```bash
git add trading_copilot/templates/index.html
git commit -m "fix: never fabricate macro context; render UNAVAILABLE instead (A-1)"
```

---

### Task 0.7: Expose the alert endpoints the UI already calls

**Files:**
- Modify: `trading_copilot/api_server.py` (add after line 219)
- Test: `tests/test_alert_endpoints.py`

Fixes [§A-11](../../../drawbacks_false_claimed.md#-a-11-three-alert-endpoints-the-ui-depends-on-do-not-exist): the UI polls three alert routes that do not exist; `ReasoningEngine.global_alerts` is populated and never exposed, and the 404 fails silently so the tray looks legitimately empty.

- [ ] **Step 1: Write the failing test**

`tests/test_alert_endpoints.py`:

```python
from fastapi.testclient import TestClient
import api_server
from reasoning_engine import ReasoningEngine

client = TestClient(api_server.app)


def setup_function():
    ReasoningEngine.global_alerts.clear()
    ReasoningEngine.global_alerts.extend([
        {"id": 2, "timestamp": 1, "symbol": "SAIL", "verdict": "CONFIRM",
         "action": "EXECUTE_LONG", "rationale": "x", "read": False},
        {"id": 1, "timestamp": 0, "symbol": "INFY", "verdict": "ABORT",
         "action": "PASS", "rationale": "y", "read": True},
    ])


def test_unread_count():
    assert client.get("/api/alerts/unread").json()["count"] == 1


def test_history_returns_all():
    body = client.get("/api/alerts/history").json()
    assert body["status"] == "success"
    assert len(body["alerts"]) == 2


def test_mark_read_flips_the_flag():
    assert client.post("/api/alerts/mark-read/2").json()["status"] == "success"
    assert client.get("/api/alerts/unread").json()["count"] == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pip install httpx && ./.venv/Scripts/python.exe -m pytest tests/test_alert_endpoints.py -v`
Expected: FAIL — all three assert on HTTP 404 payloads.

- [ ] **Step 3: Add the routes**

```python
@app.get("/api/alerts/unread")
async def alerts_unread():
    return {"count": sum(1 for a in ReasoningEngine.global_alerts if not a.get("read"))}


@app.get("/api/alerts/history")
async def alerts_history():
    return {"status": "success", "alerts": ReasoningEngine.global_alerts}


@app.post("/api/alerts/mark-read/{alert_id}")
async def alerts_mark_read(alert_id: int):
    for a in ReasoningEngine.global_alerts:
        if a.get("id") == alert_id:
            a["read"] = True
    return {"status": "success"}
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_alert_endpoints.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Remove the dead Map-Option-Tokens control**

`/api/map-option-tokens` proxies to a route that exists only in the dead `smart_api_feed.py`. Delete the
proxy at `api_server.py:121-122` and the button + handler at `index.html:127` and `:1814-1825`.

- [ ] **Step 6: Commit**

```bash
git add trading_copilot/api_server.py trading_copilot/templates/index.html tests/test_alert_endpoints.py
git commit -m "feat: expose alert endpoints; remove dead map-option-tokens control (A-11)"
```

---

### Task 0.8: Isolate per-symbol failures in the calculation loop

**Files:**
- Modify: `trading_copilot/rolling_state_engine.py:247-424`
- Test: `tests/test_loop_isolation.py`

Fixes [§A-13](../../../drawbacks_false_claimed.md#-a-13-one-bad-symbol-silently-drops-every-symbol-after-it-in-the-cycle): the `try` opens before the `for` and closes after it, so a fault on symbol *k* starves symbols *k+1…n*.

- [ ] **Step 1: Write the failing test**

`tests/test_loop_isolation.py`:

```python
import pandas as pd, pytest
from rolling_state_engine import RollingStateEngine


@pytest.mark.asyncio
async def test_one_bad_symbol_does_not_starve_the_rest(monkeypatch):
    eng = RollingStateEngine.__new__(RollingStateEngine)
    good = pd.DataFrame({"timestamp": pd.to_datetime(["2026-09-09 09:15"]),
                         "open": [1.0], "high": [1.0], "low": [1.0],
                         "close": [1.0], "volume": [1.0], "oi": [0.0]})
    eng.dfs = {"BAD": {"ltf_df": good.copy(), "htf_df": pd.DataFrame()},
               "GOOD": {"ltf_df": good.copy(), "htf_df": pd.DataFrame()}}
    eng.watchlist = {"BAD": {"symbol": "BAD"}, "GOOD": {"symbol": "GOOD"}}
    ph = {"timestamp": pd.Timestamp("2026-09-09 09:20"), "open": 1.0, "high": 1.0,
          "low": 1.0, "close": 1.0, "volume": 1.0, "oi": 0.0, "microstructure": {}}
    eng.phantom_candles = {"BAD": dict(ph), "GOOD": dict(ph)}

    seen = []

    def fake_payload(df, htf_df, token, index_df=None):
        seen.append(token)
        if token == "BAD":
            raise ValueError("synthetic failure")
        return {"token": token}

    from technical_engine import MathEngine
    monkeypatch.setattr(MathEngine, "generate_signal_payload", staticmethod(fake_payload))

    await eng.run_one_cycle()
    assert "GOOD" in seen, "GOOD was starved by BAD's failure"
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_loop_isolation.py -v`
Expected: FAIL with `AttributeError: 'RollingStateEngine' object has no attribute 'run_one_cycle'`

- [ ] **Step 3: Extract a per-cycle method with per-symbol isolation**

Split `calculate_technicals_loop` into a thin driver and a testable cycle:

```python
    async def calculate_technicals_loop(self):
        logger.info("Starting Rolling State Calculator Loop...")
        while True:
            await self.run_one_cycle()
            await asyncio.sleep(1.5)

    async def run_one_cycle(self):
        for token, phantom in list(self.phantom_candles.items()):
            try:
                self._compute_symbol(token, phantom)
            except Exception as e:
                self._failures[token] = self._failures.get(token, 0) + 1
                if self._failures[token] in (1, 10, 100):
                    logger.error(
                        f"Symbol {token} failed {self._failures[token]}x: {e}", exc_info=True)
                continue
            else:
                self._failures.pop(token, None)
            await asyncio.sleep(0)      # yield so /state is not starved (D-4)
```

Move the existing per-symbol body (lines 249–421) verbatim into `_compute_symbol(self, token, phantom)`,
returning early instead of `continue`. Initialise `self._failures = {}` in `__init__`.

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_loop_isolation.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add trading_copilot/rolling_state_engine.py tests/test_loop_isolation.py
git commit -m "fix: isolate per-symbol failures and yield between symbols (A-13, D-4)"
```

---

### Task 0.9: Correct the whale-CVD polarity check

**Files:**
- Modify: `trading_copilot/intraday_gatekeeper.py:106-114`, `trading_copilot/api_server.py:145-149`
- Test: `tests/test_gatekeeper_polarity.py`

Fixes [§A-6](../../../drawbacks_false_claimed.md#-a-6-any-long-position-is-force-closed-whenever-whale-cvd-is-merely-negative): the code tests the *sign of the level*, not a flip, so any long is force-closed whenever cumulative whale CVD is negative — which for a net-selling session is all day.

- [ ] **Step 1: Write the failing test**

`tests/test_gatekeeper_polarity.py`:

```python
from intraday_gatekeeper import IntradayGatekeeper as G

BASE_STRUCT = {"1_live_microstructure": {"flow_divergence_state": "EQUILIBRIUM_CHOP"},
               "math_setup": {}, "market_regime": {"current_regime": "TREND_EXPANSION",
                                                   "session_phase": "MORNING_SESSION"}}


def _pos(**kw):
    p = {"direction": "Long", "entry_price": 100.0, "entry_timestamp": 0,
         "stoploss": 90.0, "whale_cvd_at_entry": -500_000.0}
    p.update(kw)
    return p


def test_persistently_negative_whale_cvd_does_not_force_close(monkeypatch):
    """A long in a net-selling name must not be closed merely because the
    cumulative level is below zero."""
    monkeypatch.setattr("pipeline_guard.is_market_open", lambda: True)
    res = G.evaluate(BASE_STRUCT, {"whale_cvd_ema_1h": -480_000.0, "adv_shares": 5_000_000.0},
                     {"position": _pos()}, ltp=101.0)
    assert res["Action"] != "Close"


def test_genuine_adverse_flip_does_close(monkeypatch):
    monkeypatch.setattr("pipeline_guard.is_market_open", lambda: True)
    res = G.evaluate(BASE_STRUCT, {"whale_cvd_ema_1h": -2_000_000.0, "adv_shares": 5_000_000.0},
                     {"position": _pos()}, ltp=101.0)
    assert res["Action"] == "Close"
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_gatekeeper_polarity.py -v`
Expected: FAIL — `test_persistently_negative...` gets `Close`.

- [ ] **Step 3: Replace the check**

```python
        # Adverse whale-flow *change* since entry, normalised by average daily volume,
        # so the threshold is comparable across a ₹20 and a ₹10,000 stock.
        FLIP_THRESHOLD = 0.03           # 3% of ADV moved against the position
        entry_whale = float(position.get("whale_cvd_at_entry") or 0.0)
        adv = max(1.0, float(raw_payload.get("adv_shares") or 0.0))
        delta_norm = (whale_cvd_ema_1h - entry_whale) / adv

        polarity_flipped = (
            (direction == "Long" and delta_norm < -FLIP_THRESHOLD) or
            (direction == "Short" and delta_norm > FLIP_THRESHOLD)
        )
```

- [ ] **Step 4: Capture `whale_cvd_at_entry` when a position is saved**

In `api_server.py`, `save_position_api`:

```python
@app.post("/api/reasoning/position/save")
async def save_position_api(req: SavePositionRequest):
    norm = ReasoningEngine._normalize_symbol(req.symbol)
    pos = dict(req.user_position or {})
    if pos and "whale_cvd_at_entry" not in pos:
        for key, state in local_active_states.items():
            if key.split('|')[-1].split('-')[0] == norm:
                pos["whale_cvd_at_entry"] = state.get("whale_cvd_ema_1h", 0.0)
                break
    ReasoningEngine.user_positions[norm] = pos or None
    return {"status": "success"}
```

- [ ] **Step 5: Publish `adv_shares` in the payload**

In `rolling_state_engine._compute_symbol`, after the technicals block:

```python
                    ltf = token_data.get('ltf_df')
                    if ltf is not None and len(ltf) > 75:
                        # 20 sessions x 75 five-minute bars
                        final_payload['adv_shares'] = float(ltf['volume'].tail(1500).sum() / 20.0)
                    else:
                        final_payload['adv_shares'] = 0.0
```

- [ ] **Step 6: Run to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_gatekeeper_polarity.py -v`
Expected: PASS (2 passed)

- [ ] **Step 7: Commit**

```bash
git add trading_copilot/intraday_gatekeeper.py trading_copilot/api_server.py trading_copilot/rolling_state_engine.py tests/test_gatekeeper_polarity.py
git commit -m "fix: whale-CVD check tests normalised adverse change, not sign of level (A-6)"
```

---

### Task 0.10: Clamp LLM-returned prices server-side

**Files:**
- Modify: `trading_copilot/reasoning_engine.py:340-366`
- Test: `tests/test_ticket_clamp.py`

Fixes [§A-10](../../../drawbacks_false_claimed.md#-a-10-llm-price-output-reaches-the-ui-with-no-validation): the prompt states adjustment bounds but nothing enforces them; a hallucinated stop reaches the UI as an actionable price.

- [ ] **Step 1: Write the failing test**

`tests/test_ticket_clamp.py`:

```python
import pytest
from reasoning_engine import clamp_risk_parameters

GEO = {"calculated_entry": 100.0, "padded_stop": 98.0, "calculated_target": 105.0}


def test_entry_is_clamped_to_plus_minus_0_3_pct():
    out, err = clamp_risk_parameters({"final_entry": 120.0, "final_stop": 98.0,
                                      "final_target": 105.0}, GEO, atr15=1.0, bias="LONG")
    assert err is None
    assert out["final_entry"] == pytest.approx(100.3)


def test_stop_is_clamped_to_the_atr_band():
    out, _ = clamp_risk_parameters({"final_entry": 100.0, "final_stop": 50.0,
                                    "final_target": 105.0}, GEO, atr15=1.0, bias="LONG")
    assert out["final_stop"] == pytest.approx(97.0)      # padded_stop - 1.0*ATR


def test_inverted_geometry_is_rejected_and_falls_back():
    out, err = clamp_risk_parameters({"final_entry": 100.0, "final_stop": 106.0,
                                      "final_target": 105.0}, GEO, atr15=1.0, bias="LONG")
    assert err == "LLM_GEOMETRY_REJECTED"
    assert out["final_stop"] == 98.0                      # deterministic fallback


def test_missing_values_fall_back_to_math_geometry():
    out, err = clamp_risk_parameters({}, GEO, atr15=1.0, bias="LONG")
    assert err is None
    assert (out["final_entry"], out["final_stop"], out["final_target"]) == (100.0, 98.0, 105.0)
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_ticket_clamp.py -v`
Expected: FAIL with `ImportError: cannot import name 'clamp_risk_parameters'`

- [ ] **Step 3: Implement at module level in `reasoning_engine.py`**

```python
def clamp_risk_parameters(risk_params: dict, geo: dict, atr15: float, bias: str):
    """Enforce the adjustment bounds the prompt states but cannot guarantee.

    Returns (clamped_params, error). On error the deterministic geometry is
    returned unchanged so the operator never sees a hallucinated price.
    """
    fallback = {"final_entry": geo["calculated_entry"],
                "final_stop": geo["padded_stop"],
                "final_target": geo["calculated_target"]}
    if not risk_params:
        return fallback, None

    def _f(key, default):
        try:
            v = float(risk_params.get(key))
            return v if v > 0 else default
        except (TypeError, ValueError):
            return default

    entry = _f("final_entry", geo["calculated_entry"])
    stop = _f("final_stop", geo["padded_stop"])
    target = _f("final_target", geo["calculated_target"])

    lo_e, hi_e = geo["calculated_entry"] * 0.997, geo["calculated_entry"] * 1.003
    entry = min(max(entry, lo_e), hi_e)

    lo_s, hi_s = sorted((geo["padded_stop"] - 1.0 * atr15,
                         geo["padded_stop"] + 0.5 * atr15))
    stop = min(max(stop, lo_s), hi_s)

    if bias == "LONG":
        target = max(target, geo["calculated_target"] - 0.5 * atr15)
        ordered = stop < entry < target
    else:
        target = min(target, geo["calculated_target"] + 0.5 * atr15)
        ordered = target < entry < stop

    if not ordered:
        return fallback, "LLM_GEOMETRY_REJECTED"
    return {"final_entry": round(entry, 2), "final_stop": round(stop, 2),
            "final_target": round(target, 2)}, None
```

- [ ] **Step 4: Wire it into `analyze_stock`**

Replace the `risk_params = ticket.get("risk_parameters") or {}` block:

```python
                    geo_src = (math_setup.get("execution_geometry") or {})
                    if geo_src:
                        atr15 = float(payload_copy.get("atr_15m")
                                      or payload_copy.get("ltp", 100.0) * 0.005)
                        risk_params, geo_err = clamp_risk_parameters(
                            ticket.get("risk_parameters") or {}, geo_src, atr15,
                            math_setup.get("directional_bias", "LONG"))
                    else:
                        risk_params, geo_err = {"final_entry": 0.0, "final_stop": 0.0,
                                                "final_target": 0.0}, None
```

and add `"geometry_override": geo_err` to `ui_data` so the UI can flag it.

- [ ] **Step 5: Run to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_ticket_clamp.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add trading_copilot/reasoning_engine.py tests/test_ticket_clamp.py
git commit -m "fix: clamp LLM risk parameters server-side with fallback (A-10)"
```

---

### Task 0.11: Security and documentation hygiene

**Files:**
- Modify: `trading_copilot/api_server.py:21-27,442`, `.env.example`, `README.md`, `.gitignore`, `trading_copilot/start_all.bat`

Fixes [§E-1, §E-2, §E-3](../../../drawbacks_false_claimed.md#e--security-and-operations), [§B-21](../../../drawbacks_false_claimed.md#b1-verdicts), [§B-29](../../../drawbacks_false_claimed.md#b1-verdicts).

- [ ] **Step 1: Replace the TOTP seed in `.env.example`**

`.env.example` contains `UPSTOX_TOTP_KEY=XHRGR2YOVCEGBYT3MQEBLI6CV3POJD7L` — a real-looking base32 secret
while every other value is `your_*`. The file is currently **untracked**, so it is not yet in git history.
Replace with `UPSTOX_TOTP_KEY=your_totp_secret` and drop the dead `ANGEL_*` block. **If that value is your
live seed, rotate it in Upstox before committing anything.**

- [ ] **Step 2: Fix the README `.env` block**

README documents `UPSTOX_API_KEY` / `UPSTOX_API_SECRET`; the code reads `UPSTOX_CLIENT_ID` /
`UPSTOX_CLIENT_SECRET` and also requires `UPSTOX_MOBILE_NO`. Following the README yields `client_id=None`.
Replace the block with the keys from `.env.example`.

- [ ] **Step 3: Bind to localhost and fix CORS**

```python
# api_server.py:21-27
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# api_server.py:442
config = uvicorn.Config(app, host="127.0.0.1", port=8000,
                        log_level="warning", ws_ping_interval=None)
```

If LAN access from another device is genuinely wanted, keep `0.0.0.0` but add a shared-secret header
dependency — do not leave it open and unauthenticated.

- [ ] **Step 4: Replace `start_all.bat`**

It launches `smart_api_feed.py` (dead) and `nse_feed.py` (does not exist), and `-d .` sets CWD to
`trading_copilot`, which broke the playbook path before Task 0.2:

```bat
@echo off
REM Launch from the repo root.
wt -w 0 new-tab --title "Upstox Feed" -d . cmd /k "python trading_copilot\data_services\upstox_feed.py" ; ^
new-tab --title "News Feed" -d . cmd /k "python trading_copilot\data_services\news_feed.py" ; ^
new-tab --title "Macro Worker" -d . cmd /k "python trading_copilot\data_services\macro_worker.py" ; ^
new-tab --title "Web UI" -d . cmd /k "timeout /t 8 /nobreak && python trading_copilot\main.py"
```

- [ ] **Step 5: Add scratch files to `.gitignore`**

```gitignore
# Scratch / debug artefacts
scratch/
temp.js
scratch_script_*.js
script_test_*.js
test_*.py
grep_transcript.txt
current_diff.txt
*.tex
output.json
response.json
index_edits.txt
tab_html3.txt
```

Note: `test_*.py` at the repo root are ad-hoc scratch scripts, distinct from `tests/test_*.py` which are
tracked. Verify with `git status --short tests/` that the real tests are still staged.

- [ ] **Step 6: Verify the suite still passes**

Run: `./.venv/Scripts/python.exe -m pytest -v`
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add .env.example README.md .gitignore trading_copilot/api_server.py trading_copilot/start_all.bat
git commit -m "chore: bind localhost, scope CORS, fix env docs and launcher (E-1, E-2, E-3, B-21, B-29)"
```

---

# PHASE 1 — Record Everything

**Why now:** pure addition, no behaviour change, and every deferred day is training data lost forever. Start this the moment Phase 0 lands, and let it accumulate while Phase 2 proceeds.

**Exit criteria:** ticks and feature vectors landing on disk daily; outcome labelling bar-accurate; pending signals survive restart.

---

### Task 1.1: TickRecorder

**Files:**
- Create: `trading_copilot/journal/__init__.py`, `trading_copilot/journal/tick_recorder.py`, `tests/test_tick_recorder.py`

**Interfaces:**
- Consumes: `paths.TICKS_DIR`
- Produces: `TickRecorder(data_dir: Path, flush_n: int = 20_000)`, `.record(token, ts_ms, ltp, vtt, oi, bid1, ask1, bid_qty, ask_qty) -> None`, `.flush() -> Path | None`, `.close() -> None`

- [ ] **Step 1: Write the failing test**

`tests/test_tick_recorder.py`:

```python
import pandas as pd
from journal.tick_recorder import TickRecorder


def test_flush_writes_parquet_with_expected_schema(tmp_path):
    r = TickRecorder(tmp_path, flush_n=1_000_000)
    for i in range(5):
        r.record("NSE_EQ|SAIL", 1_757_000_000_000 + i * 250, 132.5 + i * 0.05,
                 1_000_000 + i * 500, 0.0, 132.45, 132.55, 900, 1100)
    out = r.flush()
    assert out is not None and out.exists()
    df = pd.read_parquet(out)
    assert len(df) == 5
    assert list(df.columns) == ["token", "ts_ms", "ltp", "vtt", "oi",
                                "bid1", "ask1", "bid_qty", "ask_qty"]
    assert df["vtt"].iloc[-1] == 1_002_000


def test_autoflush_at_threshold(tmp_path):
    r = TickRecorder(tmp_path, flush_n=3)
    for i in range(3):
        r.record("T", i, 1.0, i, 0.0, 0.9, 1.1, 1, 1)
    assert list(tmp_path.rglob("*.parquet")), "auto-flush did not fire"


def test_flush_on_empty_buffer_is_a_noop(tmp_path):
    assert TickRecorder(tmp_path).flush() is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_tick_recorder.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'journal'`

- [ ] **Step 3: Implement**

`trading_copilot/journal/tick_recorder.py`:

```python
"""Append-only tick capture.

Microstructure features (OBI, CVD, whale CVD, session VWAP, intraday POC) are
40-50% of the composite score and are derived from data that was previously
consumed and discarded. Without this file they can never be validated.

Volume: ~2.4M rows/day for 27 symbols; ~40-60 MB/day compressed.
"""
from __future__ import annotations

import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

_COLUMNS = ["token", "ts_ms", "ltp", "vtt", "oi", "bid1", "ask1", "bid_qty", "ask_qty"]
_IST = ZoneInfo("Asia/Kolkata")


class TickRecorder:
    def __init__(self, data_dir: Path, flush_n: int = 20_000):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._flush_n = flush_n
        self._buf: list[tuple] = []
        self._seq = 0

    def record(self, token: str, ts_ms: int, ltp: float, vtt: float, oi: float,
               bid1: float, ask1: float, bid_qty: int, ask_qty: int) -> None:
        self._buf.append((token, int(ts_ms), float(ltp), float(vtt), float(oi),
                          float(bid1), float(ask1), int(bid_qty), int(ask_qty)))
        if len(self._buf) >= self._flush_n:
            self.flush()

    def _partition(self) -> Path:
        day = datetime.datetime.now(_IST).strftime("%Y-%m-%d")
        p = self.data_dir / f"date={day}"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def flush(self) -> Path | None:
        if not self._buf:
            return None
        df = pd.DataFrame(self._buf, columns=_COLUMNS)
        self._buf.clear()
        self._seq += 1
        out = self._partition() / f"ticks_{self._seq:06d}.parquet"
        df.to_parquet(out, engine="pyarrow", compression="zstd", index=False)
        return out

    def close(self) -> None:
        self.flush()
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_tick_recorder.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add trading_copilot/journal/ tests/test_tick_recorder.py
git commit -m "feat: add append-only TickRecorder (improved §4.1)"
```

---

### Task 1.2: Wire TickRecorder into ingest

**Files:**
- Modify: `trading_copilot/rolling_state_engine.py` (`__init__`, `process_tick`), `trading_copilot/data_services/upstox_feed.py` (`start_upstox_service`)

**Interfaces:**
- Consumes: `TickRecorder` from Task 1.1
- Produces: `RollingStateEngine.recorder` attribute; a 30-second flush task.

- [ ] **Step 1: Attach the recorder**

In `RollingStateEngine.__init__`:

```python
        from journal.tick_recorder import TickRecorder
        from paths import TICKS_DIR
        self.recorder = TickRecorder(TICKS_DIR)
```

- [ ] **Step 2: Record inside `process_tick`**

Immediately after `tick_ts` is computed and before the options branch, so **every** tick is captured:

```python
        best_bid = bids[0]['price'] if bids else 0.0
        best_ask = asks[0]['price'] if asks else 0.0
        best_bid_qty = bids[0]['quantity'] if bids else 0
        best_ask_qty = asks[0]['quantity'] if asks else 0
        self.recorder.record(token, timestamp_ms, price, volume, oi,
                             best_bid, best_ask, best_bid_qty, best_ask_qty)
```

- [ ] **Step 3: Add a periodic flush task**

In `upstox_feed.start_upstox_service`, alongside `state_persistence_worker`:

```python
    async def tick_flush_worker():
        while True:
            await asyncio.sleep(30)
            await asyncio.to_thread(rolling_engine.recorder.flush)

    asyncio.create_task(tick_flush_worker())
```

- [ ] **Step 4: Verify end to end during a live session**

Run the feed for two minutes during market hours, then:

```bash
./.venv/Scripts/python.exe -c "
import pandas as pd, glob
fs = glob.glob('trading_copilot/data/ticks/date=*/ticks_*.parquet')
print('files:', len(fs))
df = pd.concat([pd.read_parquet(f) for f in fs])
print('rows:', len(df)); print('symbols:', df.token.nunique())
print(df.head().to_string())
"
```
Expected: non-zero rows across multiple symbols; `vtt` monotonically non-decreasing per token.

- [ ] **Step 5: Commit**

```bash
git add trading_copilot/rolling_state_engine.py trading_copilot/data_services/upstox_feed.py
git commit -m "feat: capture every tick to parquet"
```

---

### Task 1.3: FeatureLog

**Files:**
- Create: `trading_copilot/journal/feature_log.py`, `tests/test_feature_log.py`

**Interfaces:**
- Produces: `FeatureRecord` dataclass; `FeatureLog(data_dir: Path, flush_n: int = 2_000)`, `.write(rec: FeatureRecord) -> None`, `.flush() -> Path | None`

This is the record that makes the 102 thresholds tunable. It logs **every symbol on every gatekeeper tick**, not just signals — ~60,750 labelled rows/day rather than tens.

- [ ] **Step 1: Write the failing test**

`tests/test_feature_log.py`:

```python
import pandas as pd
from journal.feature_log import FeatureLog, FeatureRecord


def _rec(**kw):
    base = dict(ts=1_757_000_000, symbol="SAIL", config_version=1,
                features={"obi": 0.42, "vol_z_score_5m": 2.7, "rsi_5m": 61.0},
                staleness={"microstructure": 0.4, "derivatives": 271.0},
                regime="TREND_EXPANSION", session_phase="MORNING_SESSION",
                composite=0.34, decision="PROPOSED", llm_verdict=None)
    base.update(kw)
    return FeatureRecord(**base)


def test_features_are_flattened_into_columns(tmp_path):
    log = FeatureLog(tmp_path, flush_n=1_000_000)
    log.write(_rec())
    df = pd.read_parquet(log.flush())
    assert df["f_obi"].iloc[0] == 0.42
    assert df["f_vol_z_score_5m"].iloc[0] == 2.7
    assert df["stale_derivatives"].iloc[0] == 271.0
    assert df["decision"].iloc[0] == "PROPOSED"


def test_rejected_rows_are_recorded_too(tmp_path):
    """The counterfactual is the point — rejections must be logged."""
    log = FeatureLog(tmp_path, flush_n=1_000_000)
    log.write(_rec(decision="REJECTED_NEUTRAL_CONVICTION", composite=0.04))
    df = pd.read_parquet(log.flush())
    assert df["decision"].iloc[0] == "REJECTED_NEUTRAL_CONVICTION"


def test_heterogeneous_feature_sets_do_not_crash(tmp_path):
    log = FeatureLog(tmp_path, flush_n=1_000_000)
    log.write(_rec())
    log.write(_rec(features={"obi": 0.1, "new_feature": 9.9}))
    df = pd.read_parquet(log.flush())
    assert len(df) == 2
    assert df["f_new_feature"].isna().iloc[0]
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_feature_log.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'journal.feature_log'`

- [ ] **Step 3: Implement**

`trading_copilot/journal/feature_log.py`:

```python
"""Append-only feature-vector capture — the evidence layer.

Logs EVERY symbol on EVERY evaluation, including rejections, so that:
  - thresholds can be fitted rather than guessed,
  - feature importance can be measured,
  - the counterfactual (what we did NOT take) exists.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

_IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class FeatureRecord:
    ts: int
    symbol: str
    config_version: int
    features: dict[str, float]
    staleness: dict[str, float]
    regime: str
    session_phase: str
    composite: float | None
    decision: str                 # PROPOSED | REJECTED_<reason> | GATED_<reason>
    llm_verdict: str | None = None
    extra: dict[str, float] = field(default_factory=dict)

    def to_row(self) -> dict:
        row = {"ts": self.ts, "symbol": self.symbol,
               "config_version": self.config_version, "regime": self.regime,
               "session_phase": self.session_phase, "composite": self.composite,
               "decision": self.decision, "llm_verdict": self.llm_verdict}
        row.update({f"f_{k}": v for k, v in self.features.items()})
        row.update({f"stale_{k}": v for k, v in self.staleness.items()})
        row.update({f"x_{k}": v for k, v in self.extra.items()})
        return row


class FeatureLog:
    def __init__(self, data_dir: Path, flush_n: int = 2_000):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._flush_n = flush_n
        self._buf: list[dict] = []
        self._seq = 0

    def write(self, rec: FeatureRecord) -> None:
        self._buf.append(rec.to_row())
        if len(self._buf) >= self._flush_n:
            self.flush()

    def flush(self) -> Path | None:
        if not self._buf:
            return None
        df = pd.DataFrame(self._buf)      # union of keys; missing -> NaN
        self._buf.clear()
        self._seq += 1
        day = datetime.datetime.now(_IST).strftime("%Y-%m-%d")
        part = self.data_dir / f"date={day}"
        part.mkdir(parents=True, exist_ok=True)
        out = part / f"features_{self._seq:06d}.parquet"
        df.to_parquet(out, engine="pyarrow", compression="zstd", index=False)
        return out

    def close(self) -> None:
        self.flush()
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_feature_log.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add trading_copilot/journal/feature_log.py tests/test_feature_log.py
git commit -m "feat: add FeatureLog capturing every evaluation including rejections"
```

---

### Task 1.4: Wire FeatureLog into the gatekeeper loop

**Files:**
- Modify: `trading_copilot/reasoning_engine.py:424-529`

**Interfaces:**
- Consumes: `FeatureLog`, `FeatureRecord`, `paths.FEATURES_DIR`

- [ ] **Step 1: Instantiate once**

At the top of `start_global_gatekeeper_loop`:

```python
        from journal.feature_log import FeatureLog, FeatureRecord
        from paths import FEATURES_DIR
        feature_log = FeatureLog(FEATURES_DIR)
        cls._feature_log = feature_log
```

- [ ] **Step 2: Write one record per symbol per tick**

Immediately after `gatekeeper_res` is computed, **before** the debounce `continue`:

```python
                    math_setup = structured.get("math_setup", {}) or {}
                    regime_meta = structured.get("market_regime", {}) or {}
                    if math_setup.get("setup_rejected", True):
                        decision = f"REJECTED_{math_setup.get('rejection_reason', 'UNKNOWN')}"
                    elif gatekeeper_res.get("llm_authorized"):
                        decision = "PROPOSED"
                    else:
                        decision = f"GATED_{gatekeeper_res.get('math_rejection', 'UNKNOWN')}"

                    numeric = {k: float(v) for k, v in payload.items()
                               if isinstance(v, (int, float)) and not isinstance(v, bool)}

                    feature_log.write(FeatureRecord(
                        ts=int(time.time()),
                        symbol=norm_sym,
                        config_version=getattr(cls, "_config_version", 0),
                        features=numeric,
                        staleness={"microstructure": float(payload.get("data_age_s", 0.0))},
                        regime=regime_meta.get("current_regime", "UNKNOWN"),
                        session_phase=regime_meta.get("session_phase", "UNKNOWN"),
                        composite=math_setup.get("composite_score"),
                        decision=decision,
                    ))
```

- [ ] **Step 3: Flush periodically**

```python
        async def _feature_flush():
            while True:
                await asyncio.sleep(60)
                await asyncio.to_thread(feature_log.flush)

        asyncio.create_task(_feature_flush())
```

- [ ] **Step 4: Verify volume after one session**

```bash
./.venv/Scripts/python.exe -c "
import pandas as pd, glob
fs = glob.glob('trading_copilot/data/features/date=*/features_*.parquet')
df = pd.concat([pd.read_parquet(f) for f in fs])
print('rows:', len(df))
print(df.decision.value_counts())
print('feature cols:', len([c for c in df.columns if c.startswith('f_')]))
"
```
Expected: thousands of rows, a `decision` distribution dominated by `REJECTED_*`/`GATED_*`, and 80+ `f_` columns.

- [ ] **Step 5: Commit**

```bash
git add trading_copilot/reasoning_engine.py
git commit -m "feat: log a feature vector for every symbol on every evaluation"
```

---

### Task 1.5: Bar-accurate outcome labelling and pending-signal recovery

**Files:**
- Create: `trading_copilot/journal/outcome_labeller.py`, `tests/test_outcome_labeller.py`
- Modify: `trading_copilot/signal_ledger.py:76-162,131,139`

**Interfaces:**
- Produces: `label_outcome(bars: pd.DataFrame, entry_ts: int, entry: float, stop: float, target: float, bias: str, horizon_min: int, cost_pct: float) -> dict`
- Produces: `SignalLedger.recover_pending(days: int = 2) -> int`

Fixes [§C-3b/c/d](../../../drawbacks_false_claimed.md#-c-3-the-feedback-loop-measures-a-biased-sample-with-a-trivial-success-criterion): stops are sampled once a minute (missing intrabar touches), a +0.001 % move counts as a win, and pending signals are lost on restart.

- [ ] **Step 1: Write the failing test**

`tests/test_outcome_labeller.py`:

```python
import pandas as pd
from journal.outcome_labeller import label_outcome


def _bars():
    return pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-09-09 10:00", "2026-09-09 10:05",
                                      "2026-09-09 10:10", "2026-09-09 10:15"]),
        "open":  [100.0, 100.5, 100.2, 101.0],
        "high":  [100.8, 101.0,  99.6, 103.5],
        "low":   [ 99.9, 100.1,  98.9, 100.8],
        "close": [100.5, 100.2, 101.0, 103.0],
    })


TS = int(pd.Timestamp("2026-09-09 10:00").timestamp())


def test_intrabar_stop_touch_is_detected():
    """A 60s LTP sample would miss the 98.9 low; the bar's low must not."""
    out = label_outcome(_bars(), TS, entry=100.0, stop=99.0, target=105.0,
                        bias="LONG", horizon_min=60, cost_pct=0.06)
    assert out["hit_stop"] is True
    assert out["outcome"] == "STOP"


def test_target_hit_when_stop_untouched():
    out = label_outcome(_bars(), TS, entry=100.0, stop=95.0, target=103.0,
                        bias="LONG", horizon_min=60, cost_pct=0.06)
    assert out["hit_target"] is True and out["hit_stop"] is False


def test_win_requires_clearing_costs_not_merely_positive():
    flat = _bars().copy()
    for c in ("open", "high", "low", "close"):
        flat[c] = 100.01                       # +0.01%, below a 0.06% cost floor
    out = label_outcome(flat, TS, entry=100.0, stop=95.0, target=110.0,
                        bias="LONG", horizon_min=15, cost_pct=0.06)
    assert out["pnl_pct"] > 0
    assert out["directional_correct"] is False


def test_short_bias_inverts_the_comparisons():
    out = label_outcome(_bars(), TS, entry=100.0, stop=104.0, target=99.0,
                        bias="SHORT", horizon_min=60, cost_pct=0.06)
    assert out["hit_target"] is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_outcome_labeller.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

`trading_copilot/journal/outcome_labeller.py`:

```python
"""Bar-accurate outcome labelling.

The previous resolver sampled the last traded price once every 60 seconds, so
any stop touched and recovered inside a minute was missed and stop-hit rates
were systematically understated. This uses the 5-minute bar's high/low, and
requires a move to clear round-trip costs before it counts as correct.
"""
from __future__ import annotations

import pandas as pd


def label_outcome(bars: pd.DataFrame, entry_ts: int, entry: float, stop: float,
                  target: float, bias: str, horizon_min: int,
                  cost_pct: float) -> dict:
    start = pd.Timestamp(entry_ts, unit="s")
    end = start + pd.Timedelta(minutes=horizon_min)
    w = bars[(bars["timestamp"] >= start) & (bars["timestamp"] <= end)]

    result = {"hit_stop": False, "hit_target": False, "outcome": "TIMEOUT",
              "pnl_pct": 0.0, "directional_correct": False, "r_multiple": 0.0,
              "bars_seen": int(len(w))}
    if w.empty or entry <= 0:
        result["outcome"] = "NO_DATA"
        return result

    long = bias.upper() == "LONG"
    for _, b in w.iterrows():
        stop_hit = b["low"] <= stop if long else b["high"] >= stop
        tgt_hit = b["high"] >= target if long else b["low"] <= target
        if stop_hit and tgt_hit:
            # Both touched in one bar — assume the adverse side first.
            result.update(hit_stop=True, outcome="STOP")
            break
        if stop_hit:
            result.update(hit_stop=True, outcome="STOP")
            break
        if tgt_hit:
            result.update(hit_target=True, outcome="TARGET")
            break

    if result["outcome"] == "STOP":
        exit_px = stop
    elif result["outcome"] == "TARGET":
        exit_px = target
    else:
        exit_px = float(w["close"].iloc[-1])

    gross = ((exit_px - entry) / entry * 100.0) if long else ((entry - exit_px) / entry * 100.0)
    result["pnl_pct"] = round(gross, 4)
    result["directional_correct"] = bool(gross > cost_pct)

    risk = abs(entry - stop)
    if risk > 0:
        result["r_multiple"] = round(((exit_px - entry) if long else (entry - exit_px)) / risk, 3)
    return result
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_outcome_labeller.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Add pending-signal recovery**

In `signal_ledger.py`:

```python
    @classmethod
    def recover_pending(cls, days: int = 2) -> int:
        """Rebuild the in-memory pending set after a restart.

        Previously _pending_signals was memory-only, so every signal younger
        than the resolution horizon at shutdown stayed PENDING forever and was
        silently excluded from all statistics.
        """
        restored = 0
        for rec in cls.load_all_signals(last_n_days=days):
            if rec.get("outcome", {}).get("status") != "PENDING":
                continue
            ts = rec["timestamp"]
            cls._pending_signals[rec["signal_id"]] = {
                "record": rec, "target_30m": ts + 1800, "target_60m": ts + 3600,
                "resolved_30m": False, "resolved_60m": False,
            }
            restored += 1
        logger.info(f"Recovered {restored} pending signals from disk.")
        return restored
```

Call it at the top of `start_outcome_resolver`, before the `while True`.

- [ ] **Step 6: Point the resolver at bar data**

Replace the LTP-sampling geometric check (lines 107–125) with a call to `label_outcome`, sourcing bars
from the owning `RollingStateEngine`'s `ltf_df` for the symbol.

- [ ] **Step 7: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -v`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add trading_copilot/journal/outcome_labeller.py trading_copilot/signal_ledger.py tests/test_outcome_labeller.py
git commit -m "feat: bar-accurate outcome labelling with cost floor and restart recovery (C-3)"
```

---

# PHASE 2 — Purity, Config, Single-Writer

**Exit criteria:** all 102 thresholds live in YAML; `evaluate()` is a pure function under test; the display path cannot mutate decision state; ingest is single-writer; the perf hotspots are gone.

---

### Task 2.1: PolicyConfig

**Files:**
- Create: `trading_copilot/config/policy_v1.yaml`, `trading_copilot/core/__init__.py`, `trading_copilot/core/policy_config.py`, `tests/test_policy_config.py`

**Interfaces:**
- Produces: `PolicyConfig` (frozen dataclass) with `.version`, `.semantic`, `.conviction`, `.gates`, `.horizon`; `load_policy(path: Path | None = None) -> PolicyConfig`

- [ ] **Step 1: Write the failing test**

`tests/test_policy_config.py`:

```python
import pytest
from core.policy_config import load_policy, PolicyConfig


def test_default_config_loads_and_is_versioned():
    cfg = load_policy()
    assert isinstance(cfg, PolicyConfig)
    assert cfg.version >= 1


def test_known_thresholds_match_current_code_constants():
    """v1 must reproduce today's behaviour exactly — this is a refactor, not a
    retune. Any change of behaviour belongs in v2 after replay evidence."""
    cfg = load_policy()
    assert cfg.semantic["vol_z_shock"] == 2.5           # semantic_tagger.py:82
    assert cfg.semantic["obi_extreme"] == 0.60          # semantic_tagger.py:92
    assert cfg.conviction["bias_threshold"] == 0.15     # conviction_scorer.py:151
    assert cfg.gates["min_stat_edge"] == 0.05           # conviction_scorer.py:274
    assert cfg.gates["regime_dampening"]["RANGE_BOUND_CHOP"] == 0.6


def test_missing_required_key_raises():
    with pytest.raises(ValueError, match="missing required"):
        PolicyConfig.from_dict({"version": 1, "semantic": {}})


def test_config_is_immutable():
    cfg = load_policy()
    with pytest.raises(Exception):
        cfg.version = 99
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_policy_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'core'`

- [ ] **Step 3: Author `policy_v1.yaml`**

Transcribe every constant from `semantic_tagger.py`, `regime_manager.py`, `conviction_scorer.py`,
`intraday_gatekeeper.py`, `mtf_extractor.py`. Each entry carries a source comment.

```yaml
version: 1

semantic:
  vol_z_shock: 2.5              # semantic_tagger.py:82
  vol_z_elevated: 1.0           # semantic_tagger.py:84
  vol_z_suppressed: -1.0        # semantic_tagger.py:86
  obi_extreme: 0.60             # semantic_tagger.py:92
  obi_moderate: 0.20            # semantic_tagger.py:95
  vwap_band_elevated: 0.75      # semantic_tagger.py:58
  vwap_band_extreme: 1.50       # semantic_tagger.py:55
  whale_slope_gate_adv_frac: 0.002   # replaces the unnormalised `> 50` at :67
  flow_vwap_gate_atr_frac: 0.4  # semantic_tagger.py:67
  iv_pct_extreme: 90            # semantic_tagger.py:120
  iv_pct_elevated: 75           # semantic_tagger.py:122
  iv_pct_compressed: 25         # semantic_tagger.py:124
  gravity_imminent_atr: 0.3     # semantic_tagger.py:135
  gravity_escape_atr: 2.0       # semantic_tagger.py:137
  pcr_pct_high: 80              # semantic_tagger.py:146
  pcr_pct_low: 20               # semantic_tagger.py:148
  proximity_test_atr: 0.5       # semantic_tagger.py:188
  proximity_approach_atr: 1.0   # semantic_tagger.py:190
  alpha_strong: 2.0             # semantic_tagger.py:194
  alpha_moderate: 0.5           # semantic_tagger.py:196

regime:
  memory_len: 12                # regime_manager.py:9
  hysteresis_agreement: 2       # regime_manager.py:127 (2 of last 3)
  hysteresis_window: 3

conviction:
  bias_threshold: 0.15          # conviction_scorer.py:151
  micro_divisor: 5.0            # conviction_scorer.py:95
  deriv_divisor: 3.0            # conviction_scorer.py:110
  struct_divisor: 3.0           # conviction_scorer.py:136
  volume_shock_multiplier: 1.5  # conviction_scorer.py:93
  whipsaw_flip_limit: 3         # conviction_scorer.py:159
  whipsaw_penalty: 0.5          # conviction_scorer.py:160
  min_reward_atr15: 1.5         # conviction_scorer.py:237
  entry_offset_atr5: 0.2        # conviction_scorer.py:241
  slippage_atr5: 0.1            # conviction_scorer.py:262
  atr_mult_by_iv_regime:        # conviction_scorer.py:223-226
    EXTREME_EXPANSION: 1.00
    ELEVATED_VOLATILITY: 0.75
    PREMIUM_COMPRESSION: 0.30
    DEFAULT: 0.50
  weights:                      # conviction_scorer.py:7-14
    TREND_EXPANSION:         {micro: 0.45, struct: 0.15, deriv: 0.15, cat: 0.25}
    VOLATILITY_EXPANSION:    {micro: 0.50, struct: 0.30, deriv: 0.10, cat: 0.10}
    RANGE_BOUND_CHOP:        {micro: 0.20, struct: 0.30, deriv: 0.40, cat: 0.10}
    PRE_BREAKOUT_SQUEEZE:    {micro: 0.30, struct: 0.40, deriv: 0.20, cat: 0.10}
    MEAN_REVERSION_IMMINENT: {micro: 0.40, struct: 0.20, deriv: 0.30, cat: 0.10}
    DEFAULT:                 {micro: 0.40, struct: 0.25, deriv: 0.20, cat: 0.15}

gates:
  min_stat_edge: 0.05           # conviction_scorer.py:274
  stop_proximity_pct: 0.5       # intraday_gatekeeper.py:92
  failure_to_launch_min: 45     # intraday_gatekeeper.py:117
  stagnation_min: 30            # intraday_gatekeeper.py:122
  stagnation_drift_pct: 0.2     # intraday_gatekeeper.py:122
  whale_flip_adv_frac: 0.03     # Task 0.9
  regime_dampening:             # intraday_gatekeeper.py:158-167
    LUNCH_CHOP_HIGH_VOL: 0.85
    LUNCH_CHOP_NORMAL: 0.65
    RANGE_BOUND_CHOP: 0.6
    TRANSITIONAL_DRIFT: 0.7

horizon:                        # Task 3.4 makes these authoritative
  primary_minutes: 90
  entry_cutoff_ist: "13:45"
  square_off_ist: "15:20"
  measure_at_minutes: [30, 90]
```

- [ ] **Step 4: Implement the loader**

`trading_copilot/core/policy_config.py`:

```python
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
```

- [ ] **Step 5: Run to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_policy_config.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add trading_copilot/config/ trading_copilot/core/ tests/test_policy_config.py
git commit -m "feat: externalise all 102 decision thresholds into versioned YAML"
```

---

### Task 2.2: Split read from write (`advance_state`)

**Files:**
- Modify: `trading_copilot/reasoning_engine.py:57-119`, `trading_copilot/regime_manager.py`, `trading_copilot/conviction_scorer.py`, `trading_copilot/api_server.py:370`
- Test: `tests/test_state_isolation.py`

Fixes [§A-4](../../../drawbacks_false_claimed.md#-a-4-the-regime-fsm-and-whipsaw-shield-are-defeated-by-a-second-20-faster-caller): the 2 Hz display call mutates the same FSM the 0.1 Hz decision loop depends on, collapsing the hysteresis window from 30 s to 1.5 s and firing the whipsaw shield spuriously.

**Interfaces:**
- Produces: `RegimeManager.peek_regime() -> dict`; `ConvictionScorer.score_setup(..., advance_state: bool = False)`; `ReasoningEngine.build_structured_payload(..., advance_state: bool = False)`

- [ ] **Step 1: Write the failing test**

`tests/test_state_isolation.py`:

```python
from regime_manager import RegimeManagerRegistry
from conviction_scorer import ConvictionScorerRegistry

PAYLOAD = {"current_time": "10:30 am", "ltp": 100.0, "timestamp": 1,
           "1_live_microstructure": {"flow_divergence_state": "EQUILIBRIUM_CHOP",
                                     "volume_regime": "NORMAL_DRIFT",
                                     "fractal_alignment": "CONFLICTING_CHOP",
                                     "elasticity_risk": "EQUILIBRIUM",
                                     "kinetic_divergence": "MOMENTUM_CONFIRMED",
                                     "volatility_state": "NORMAL_RANGING"},
           "2_derivatives_matrix_52w": {}, "3_local_structural_edge_20d": {}}


def test_peek_does_not_grow_the_regime_memory(monkeypatch):
    monkeypatch.setattr("pipeline_guard.is_market_open", lambda: True)
    m = RegimeManagerRegistry.get_or_create("PEEKTEST")
    m.determine_regime(dict(PAYLOAD))
    n = len(m.memory.buffer)
    for _ in range(50):
        m.peek_regime()
    assert len(m.memory.buffer) == n, "peek mutated the hysteresis buffer"


def test_peek_does_not_advance_epoch_counter(monkeypatch):
    monkeypatch.setattr("pipeline_guard.is_market_open", lambda: True)
    m = RegimeManagerRegistry.get_or_create("EPOCHTEST")
    m.determine_regime(dict(PAYLOAD))
    before = m.epochs_in_regime
    for _ in range(20):
        m.peek_regime()
    assert m.epochs_in_regime == before


def test_scoring_without_advance_does_not_count_polarity_flips():
    s = ConvictionScorerRegistry.get_or_create("FLIPTEST")
    s.previous_bias = "LONG"
    before = s.polarity_flips_today
    for _ in range(10):
        s.score_setup({"market_regime": {"current_regime": "TREND_EXPANSION"},
                       "1_live_microstructure": {"flow_divergence_state":
                                                 "MOMENTUM_CONFIRMED_BEARISH"}},
                      {"ltp": 100.0}, advance_state=False)
    assert s.polarity_flips_today == before
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_state_isolation.py -v`
Expected: FAIL — `AttributeError: 'RegimeManager' object has no attribute 'peek_regime'`

- [ ] **Step 3: Add `peek_regime`**

```python
    def peek_regime(self) -> dict:
        """Read current regime WITHOUT mutating memory, epoch count, or FSM state."""
        from pipeline_guard import is_market_open
        if not is_market_open():
            return {"current_regime": "MARKET_CLOSED", "session_phase": "CLOSE_AUCTION",
                    "regime_age_epochs": 0, "just_transitioned": False}
        return {"current_regime": self.current_regime,
                "session_phase": self._get_session_phase(
                    self.memory.buffer[-1]["current_time"] if self.memory.buffer else ""),
                "regime_age_epochs": self.epochs_in_regime,
                "just_transitioned": False}
```

- [ ] **Step 4: Guard the ConvictionScorer mutations**

Change the signature to `score_setup(self, semantic_payload, flat_telemetry, advance_state: bool = False)`
and wrap the two mutating blocks (lines 156–168):

```python
        if advance_state:
            if (candidate_bias in ("LONG", "SHORT") and self.previous_bias in ("LONG", "SHORT")
                    and candidate_bias != self.previous_bias):
                self.polarity_flips_today += 1

            if self.polarity_flips_today >= 3:
                composite *= 0.5
                self.polarity_flips_today = 0
                if composite >= 0.15:    candidate_bias = "LONG"
                elif composite <= -0.15: candidate_bias = "SHORT"
                else:                    candidate_bias = "NEUTRAL"

            if candidate_bias != "NEUTRAL":
                self.previous_bias = candidate_bias
```

- [ ] **Step 5: Thread the flag through `build_structured_payload`**

```python
    @classmethod
    def build_structured_payload(cls, symbol, payload, user_position=None,
                                 user_intent=None, *, advance_state: bool = False):
        ...
        manager = RegimeManagerRegistry.get_or_create(symbol)
        regime_metadata = (manager.determine_regime(tactical_payload) if advance_state
                           else manager.peek_regime())
        tactical_payload["market_regime"] = regime_metadata

        scorer = ConvictionScorerRegistry.get_or_create(symbol)
        math_setup = scorer.score_setup(tactical_payload, payload,
                                        advance_state=advance_state)
```

- [ ] **Step 6: Set the flag at the two call sites**

- `api_server.py:370` — leave as the default (`advance_state=False`), display only.
- `reasoning_engine.py:451` — `cls.build_structured_payload(sym, payload, current_pos, advance_state=True)`.

- [ ] **Step 7: Run to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_state_isolation.py -v`
Expected: PASS (3 passed)

- [ ] **Step 8: Commit**

```bash
git add trading_copilot/reasoning_engine.py trading_copilot/regime_manager.py trading_copilot/conviction_scorer.py trading_copilot/api_server.py tests/test_state_isolation.py
git commit -m "fix: display path can no longer mutate decision state (A-4)"
```

---

### Task 2.3: Wire MTFFeatureExtractor

**Files:**
- Modify: `trading_copilot/rolling_state_engine.py` (`_compute_symbol`)
- Test: `tests/test_mtf_wiring.py`

Fixes [§A-5](../../../drawbacks_false_claimed.md#-a-5-two-of-the-five-market-regimes-can-never-occur): `MTFFeatureExtractor.extract_all` is never called, so four semantic fields are permanently pinned to defaults, making `PRE_BREAKOUT_SQUEEZE` and `MEAN_REVERSION_IMMINENT` unreachable and biasing everything toward `RANGE_BOUND_CHOP`.

- [ ] **Step 1: Write the failing test**

`tests/test_mtf_wiring.py`:

```python
from mtf_extractor import MTFFeatureExtractor


def test_squeeze_state_is_reachable():
    payload = {"bb_upper_15m": 101.0, "bb_lower_15m": 100.0, "atr_15m": 1.0}
    out = MTFFeatureExtractor.extract_all(payload, ltp=100.0)
    assert out["volatility_state"] == "15M_30M_COILING_SQUEEZE"


def test_overstretched_state_is_reachable():
    payload = {"ema_21_1h": 100.0, "atr_1h": 1.0}
    out = MTFFeatureExtractor.extract_all(payload, ltp=104.0)   # 4 ATR above
    assert out["elasticity_risk"] == "OVERSTRETCHED_MEAN_REVERSION_RISK_DOWN"


def test_rolling_engine_publishes_the_four_mtf_fields():
    from rolling_state_engine import RollingStateEngine
    import inspect
    src = inspect.getsource(RollingStateEngine._compute_symbol)
    assert "MTFFeatureExtractor" in src, "extractor is still not wired in"
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_mtf_wiring.py -v`
Expected: the third test FAILS.

- [ ] **Step 3: Wire it in**

In `_compute_symbol`, after the omni metrics are merged and before `TerminalDashboard.update_state`:

```python
                    from mtf_extractor import MTFFeatureExtractor
                    final_payload.update(
                        MTFFeatureExtractor.extract_all(final_payload, final_payload['ltp']))
```

- [ ] **Step 4: Remove the dead `bandwidth` variable**

`mtf_extractor.py:54` computes `bandwidth` and never uses it; delete the line and the stale comment
admitting uncertainty about the formula.

- [ ] **Step 5: Run to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_mtf_wiring.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Confirm regime reachability in the feature log**

After one session, check that the previously-impossible regimes now appear:

```bash
./.venv/Scripts/python.exe -c "
import pandas as pd, glob
df = pd.concat([pd.read_parquet(f) for f in glob.glob('trading_copilot/data/features/date=*/*.parquet')])
print(df.regime.value_counts())
"
```
Expected: `PRE_BREAKOUT_SQUEEZE` and/or `MEAN_REVERSION_IMMINENT` present with non-zero counts.

- [ ] **Step 7: Commit**

```bash
git add trading_copilot/rolling_state_engine.py trading_copilot/mtf_extractor.py tests/test_mtf_wiring.py
git commit -m "fix: wire MTFFeatureExtractor, restoring two unreachable regimes (A-5)"
```

---

### Task 2.4: Queue-based single-writer ingest

**Files:**
- Modify: `trading_copilot/data_services/upstox_feed.py:236-338`, `trading_copilot/rolling_state_engine.py`
- Test: `tests/test_ingest_queue.py`

Implements [improved §4.10](../../../improved_architecture_and_features.md#410-make-ingestion-genuinely-single-writer). Three OS threads currently mutate `phantom_candles` and `ltf_df` while the asyncio loop reads them, defended only by an incorrect GIL-safety comment.

**Interfaces:**
- Produces: `parse_tick(instrument_key: str, feed: dict, reverse_map: dict) -> Tick | None`; `RollingStateEngine.ingest_loop()`; `RollingStateEngine.queue_depth` property.

- [ ] **Step 1: Write the failing test**

`tests/test_ingest_queue.py`:

```python
import asyncio, pytest
from data_services.upstox_feed import parse_tick


def test_parse_tick_extracts_book_and_volume():
    feed = {"fullFeed": {"marketFF": {"ltpc": {"ltp": 132.5}, "vtt": 1_000_000, "oi": 0,
                                       "marketLevel": {"bidAskQuote": [
                                           {"bq": 900, "bp": 132.45, "aq": 1100, "ap": 132.55}]}}}}
    t = parse_tick("NSE_EQ|INE114A01011", feed, {"NSE_EQ|INE114A01011": "NSE_EQ|SAIL"})
    assert t.token == "NSE_EQ|SAIL"
    assert t.price == 132.5 and t.volume == 1_000_000
    assert t.bids[0]["quantity"] == 900 and t.asks[0]["price"] == 132.55


def test_parse_tick_returns_none_without_price():
    assert parse_tick("X", {"fullFeed": {"marketFF": {"ltpc": {"ltp": 0}}}}, {}) is None


@pytest.mark.asyncio
async def test_ingest_loop_drains_the_queue():
    from rolling_state_engine import RollingStateEngine
    eng = RollingStateEngine.__new__(RollingStateEngine)
    eng.tick_q = asyncio.Queue()
    eng.dfs, eng.watchlist, eng.phantom_candles = {}, {}, {}
    eng._failures, eng.recorder = {}, None
    seen = []
    eng.process_tick = lambda **kw: seen.append(kw["token"])

    await eng.tick_q.put({"token": "A", "timestamp_ms": 1, "price": 1.0,
                          "volume": 1.0, "oi": 0.0, "greeks": None,
                          "bids": [], "asks": []})
    task = asyncio.create_task(eng.ingest_loop())
    await asyncio.sleep(0.05)
    task.cancel()
    assert seen == ["A"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_ingest_queue.py -v`
Expected: FAIL with `ImportError: cannot import name 'parse_tick'`

- [ ] **Step 3: Extract `parse_tick` as a module-level pure function**

Move the flattening logic from `_on_market_update` (lines 253–299) into:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class Tick:
    token: str; timestamp_ms: int; price: float; volume: float; oi: float
    greeks: dict | None; bids: list; asks: list


def parse_tick(instrument_key: str, feed_data: dict, reverse_map: dict) -> Tick | None:
    full = feed_data.get("fullFeed", {}) or {}
    idx = feed_data.get("indexFF", {}) or {}
    mff = full.get("marketFF", {}) or {}
    ltpc = mff.get("ltpc", idx.get("ltpc", {})) or {}
    ltp = float(ltpc.get("ltp", 0) or 0)
    if ltp <= 0:
        return None

    bids, asks = [], []
    level = full.get("marketLevel", mff.get("marketLevel", {})) or {}
    for q in level.get("bidAskQuote", []) or []:
        bids.append({"quantity": int(q.get("bq", q.get("bidQ", 0)) or 0),
                     "price": float(q.get("bp", q.get("bidP", 0)) or 0)})
        asks.append({"quantity": int(q.get("aq", q.get("askQ", 0)) or 0),
                     "price": float(q.get("ap", q.get("askP", 0)) or 0)})

    return Tick(
        token=reverse_map.get(instrument_key, instrument_key),
        timestamp_ms=int(time.time() * 1000),
        price=ltp,
        volume=float(full.get("vtt", mff.get("vtt", 0)) or 0),
        oi=float(mff.get("oi", 0) or 0),
        greeks=feed_data.get("optionGreeks", full.get("optionGreeks")) or None,
        bids=bids, asks=asks,
    )
```

- [ ] **Step 4: WS threads enqueue only**

```python
    def _on_market_update(self, message):
        if not is_market_open():
            return
        feeds = message.get("feeds")
        if not isinstance(feeds, dict):
            return
        for ikey, feed in feeds.items():
            tick = parse_tick(ikey, feed, self.reverse_map)
            if tick is not None and self.rolling_engine is not None:
                self.loop.call_soon_threadsafe(
                    self.rolling_engine.tick_q.put_nowait, tick)
```

Capture `self.loop = asyncio.get_running_loop()` in `start_multiplexer` before starting the threads,
and mark the threads `daemon=True`.

- [ ] **Step 5: Add the single consumer**

```python
    async def ingest_loop(self):
        while True:
            tick = await self.tick_q.get()
            try:
                d = tick if isinstance(tick, dict) else vars(tick)
                if self.recorder is not None:
                    self.recorder.record(d["token"], d["timestamp_ms"], d["price"],
                                         d["volume"], d["oi"],
                                         d["bids"][0]["price"] if d["bids"] else 0.0,
                                         d["asks"][0]["price"] if d["asks"] else 0.0,
                                         d["bids"][0]["quantity"] if d["bids"] else 0,
                                         d["asks"][0]["quantity"] if d["asks"] else 0)
                self.process_tick(**{k: d[k] for k in
                                     ("token", "timestamp_ms", "price", "volume",
                                      "oi", "greeks", "bids", "asks") if k in d})
            except Exception as e:
                logger.error(f"ingest_loop error: {e}", exc_info=True)
            finally:
                self.tick_q.task_done()

    @property
    def queue_depth(self) -> int:
        return self.tick_q.qsize()
```

Initialise `self.tick_q = asyncio.Queue(maxsize=100_000)` in `__init__`, and
`asyncio.create_task(rolling_engine.ingest_loop())` in `start_upstox_service`.

- [ ] **Step 6: Publish queue depth on `/state`**

```python
@app.get("/state")
async def get_state():
    from diagnostic_ui import TerminalDashboard
    eng = getattr(app.state, "rolling_engine", None)
    return make_json_serializable({
        "active_states": TerminalDashboard.active_states,
        "queue_depth": eng.queue_depth if eng else -1,
    })
```

- [ ] **Step 7: Run to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_ingest_queue.py -v`
Expected: PASS (3 passed)

- [ ] **Step 8: Commit**

```bash
git add trading_copilot/data_services/upstox_feed.py trading_copilot/rolling_state_engine.py tests/test_ingest_queue.py
git commit -m "refactor: single-writer queue-based ingest with queue-depth metric (improved §4.10)"
```

---

### Task 2.5: Stream supervision and staleness

**Files:**
- Modify: `trading_copilot/data_services/upstox_feed.py`, `trading_copilot/rolling_state_engine.py`, `trading_copilot/templates/index.html`

Fixes [§A-12](../../../drawbacks_false_claimed.md#-a-12-no-websocket-reconnection--a-dropped-stream-is-silent-and-permanent) and implements [improved §4.8](../../../improved_architecture_and_features.md#48-make-data-staleness-a-first-class-signal).

- [ ] **Step 1: Supervise each stream with exponential backoff**

```python
    async def _supervise(self, name, streamer, keys, mode):
        backoff = 1
        while True:
            self._setup_stream(streamer, name, keys, mode)
            t = threading.Thread(target=streamer.connect, daemon=True)
            t.start()
            while t.is_alive():
                await asyncio.sleep(1)
                backoff = 1                      # healthy — reset
            logger.error(f"{name} stream died; reconnecting in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
```

Replace the three bare `threading.Thread(...).start()` calls with `asyncio.create_task(self._supervise(...))`.

- [ ] **Step 2: Track per-token last-tick time**

In `process_tick`: `self.last_tick_ts[token] = time.time()`.
In `_compute_symbol`: `final_payload['data_age_s'] = round(time.time() - self.last_tick_ts.get(token, 0), 1)`.

- [ ] **Step 3: Reject on stale microstructure**

In `IntradayGatekeeper.evaluate`, immediately after the market-open guard:

```python
        age = float(raw_payload.get("data_age_s", 0.0) or 0.0)
        if age > 15.0:
            return {"Action": "Wait", "Priority_Score": 0, "Confidence_Score": 0,
                    "llm_authorized": False, "math_rejection": f"STALE_DATA_{int(age)}s"}
```

- [ ] **Step 4: Surface it in the UI**

In `updateMarketMatrix`, add a per-row age chip, and a global banner when any symbol exceeds 15 s during
market hours:

```js
const stale = Object.values(payload.global_state)
    .filter(s => s.market_state === 'LIVE' && (s.data_age_s || 0) > 15);
const banner = document.getElementById('stale-banner');
if (banner) {
    banner.classList.toggle('hidden', stale.length === 0);
    if (stale.length) banner.textContent =
        `⚠ FEED STALE — ${stale.length} symbol(s) have not ticked in >15s`;
}
```

Add the banner element near `status-dot` with class `bg-amber-600 text-black font-bold p-2 text-center`.

- [ ] **Step 5: Manual verification**

Kill the `upstox_feed.py` process mid-session. Expected: the amber banner appears within ~20 s, and the
gatekeeper emits `STALE_DATA_*` rejections rather than acting on frozen data.

- [ ] **Step 6: Commit**

```bash
git add trading_copilot/data_services/upstox_feed.py trading_copilot/rolling_state_engine.py trading_copilot/intraday_gatekeeper.py trading_copilot/templates/index.html
git commit -m "feat: supervise streams, propagate staleness, block stale decisions (A-12)"
```

---

### Task 2.6: Retire the performance hotspots

**Files:**
- Modify: `trading_copilot/performance_analyzer.py`, `trading_copilot/rolling_state_engine.py`, `trading_copilot/technical_engine.py:104,348`
- Test: `tests/test_perf_hotspots.py`

Fixes [§D-1](../../../drawbacks_false_claimed.md#-d-1-the-ledger-is-re-read-from-disk-100-times-per-second) (~100 ledger scans/sec), [§D-2](../../../drawbacks_false_claimed.md#-d-2-disk-io-inside-the-hot-calculation-loop), [§D-3](../../../drawbacks_false_claimed.md#-d-3-full-recomputation-of-1650-row-indicators-to-use-one-value).

- [ ] **Step 1: Write the failing test**

`tests/test_perf_hotspots.py`:

```python
import numpy as np, pandas as pd, pytest
from performance_analyzer import PerformanceAnalyzer
from technical_engine import MathEngine


def test_feedback_payload_is_cached(monkeypatch):
    calls = {"n": 0}

    def counting_load(last_n_days=30):
        calls["n"] += 1
        return []

    monkeypatch.setattr("signal_ledger.SignalLedger.load_all_signals", counting_load)
    PerformanceAnalyzer.invalidate_cache()
    for _ in range(50):
        PerformanceAnalyzer.get_feedback_payload(14)
    assert calls["n"] <= 2, f"ledger re-read {calls['n']} times; cache not working"


def test_volume_profile_matches_the_loop_implementation():
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"high": rng.uniform(100, 110, 500),
                       "low": rng.uniform(90, 100, 500),
                       "close": rng.uniform(95, 105, 500),
                       "volume": rng.uniform(1e3, 1e5, 500)})
    out = MathEngine.calc_volume_profile_high_fidelity(df, bins=100)
    assert out["rolling_20d_value_area_low"] <= out["rolling_20d_poc_price"]
    assert out["rolling_20d_poc_price"] <= out["rolling_20d_value_area_high"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_perf_hotspots.py -v`
Expected: `test_feedback_payload_is_cached` FAILS (ledger read ~100 times).

- [ ] **Step 3: Add a TTL cache to `PerformanceAnalyzer`**

```python
import time

class PerformanceAnalyzer:
    _cache: dict | None = None
    _cache_ts: float = 0.0
    _TTL = 300.0

    @classmethod
    def invalidate_cache(cls) -> None:
        cls._cache, cls._cache_ts = None, 0.0

    @staticmethod
    def get_feedback_payload(last_n_days: int = 14) -> dict:
        cls = PerformanceAnalyzer
        now = time.monotonic()
        if cls._cache is not None and (now - cls._cache_ts) < cls._TTL:
            return cls._cache
        signals = SignalLedger.load_all_signals(last_n_days)      # ONE load
        metrics = cls._compute_metrics(signals)
        if metrics.get("total_resolved", 0) == 0:
            cls._cache, cls._cache_ts = {}, now
            return {}
        regime_acc = cls._regime_accuracy_from(signals)            # reuse, don't reload
        ...
        cls._cache, cls._cache_ts = payload, now
        return payload
```

Add `_regime_accuracy_from(signals)` and `_symbol_accuracy_from(signals)` that group an
already-loaded list, and make `compute_regime_accuracy` / `compute_symbol_accuracy` / `compute_dashboard`
call them after a single `load_all_signals`.

- [ ] **Step 4: Hoist the hot-loop disk reads**

In `RollingStateEngine.__init__`:

```python
        self._baselines = {}
        self._baselines_mtime = 0.0
        self._flow_state = {}
        self._flow_mtime = 0.0

    def _get_baselines(self) -> dict:
        from paths import MACRO_BASELINES_PATH
        try:
            m = MACRO_BASELINES_PATH.stat().st_mtime
        except FileNotFoundError:
            return {}
        if m != self._baselines_mtime:
            with open(MACRO_BASELINES_PATH, "r") as f:
                self._baselines = json.load(f)
            self._baselines_mtime = m
        return self._baselines
```

Replace the per-symbol `open(baselines_path)` at line 316 with `self._get_baselines()`, and apply the
same mtime pattern to `InstitutionalFlowTracker.load_state()` at line 370.

- [ ] **Step 5: Vectorise the two hot loops**

`technical_engine.py:348` — replace the Python accumulation loop:

```python
            vol_profile = np.bincount(indices, weights=np.nan_to_num(volumes),
                                      minlength=bins)[:bins]
```

`technical_engine.py:104` — replace `df.apply(calc_tod_z, axis=1)`:

```python
        tod_mean = df['time'].map({k: v['mean'] for k, v in time_stats.items()})
        tod_std = df['time'].map({k: v['std'] for k, v in time_stats.items()})
        z_tod = (df['volume'] - tod_mean) / tod_std.where(tod_std > 0)
        z_roll = (df['volume'] - df['rolling_mean']) / df['rolling_std'].where(df['rolling_std'] > 0)
        df['vol_z_score'] = z_tod.fillna(z_roll).fillna(0.0)
```

- [ ] **Step 6: Slice history before computing indicators**

In `_compute_symbol`, cap the window fed to `MathEngine` — RSI-14/MACD-26/BB-20 are numerically identical
beyond ~400 bars:

```python
                    temp_df = pd.concat([target_df.tail(400), phantom_df], ignore_index=True)
```

- [ ] **Step 7: Move the 6 MB cache write off the loop**

```python
    async def state_persistence_worker():
        while True:
            await asyncio.sleep(300)
            if is_market_open():
                await asyncio.to_thread(rolling_engine.save_cache)
```

- [ ] **Step 8: Run to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_perf_hotspots.py -v`
Expected: PASS (2 passed)

- [ ] **Step 9: Commit**

```bash
git add trading_copilot/performance_analyzer.py trading_copilot/rolling_state_engine.py trading_copilot/technical_engine.py trading_copilot/data_services/upstox_feed.py tests/test_perf_hotspots.py
git commit -m "perf: TTL-cache ledger, hoist disk IO, vectorise hot loops (D-1, D-2, D-3, D-4)"
```

---

### Task 2.7: Session-anchored resampling

**Files:**
- Modify: `trading_copilot/technical_engine.py:411-412`
- Test: `tests/test_resample_anchor.py`

Fixes [§C-4](../../../drawbacks_false_claimed.md#-c-4-several-indicators-are-dimensionally-or-structurally-unsound): `df.resample('4h')` is midnight-anchored, producing a 2 h 45 m "4-hour" bar against an NSE session of 09:15–15:30.

- [ ] **Step 1: Write the failing test**

`tests/test_resample_anchor.py`:

```python
import pandas as pd
from technical_engine import MathEngine


def test_hourly_bars_start_at_the_session_open():
    idx = pd.date_range("2026-09-09 09:15", "2026-09-09 15:30", freq="5min")
    df = pd.DataFrame({"timestamp": idx, "open": 100.0, "high": 101.0,
                       "low": 99.0, "close": 100.5, "volume": 1000.0})
    omni = MathEngine.generate_omni_dataframes(df)
    first = omni["1h"].index[0]
    assert (first.hour, first.minute) == (9, 15), f"1h bar anchored at {first}"
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_resample_anchor.py -v`
Expected: FAIL — first bar at 09:00.

- [ ] **Step 3: Anchor the resample to the session open**

```python
            session_origin = pd.Timestamp(df.index[0].date()) + pd.Timedelta(hours=9, minutes=15)
            for freq, label in [('5min', '5m'), ('15min', '15m'), ('30min', '30m'),
                                ('1h', '1h'), ('4h', '4h')]:
                omni[label] = df.resample(freq, origin=session_origin).agg(agg_dict).dropna()
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_resample_anchor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trading_copilot/technical_engine.py tests/test_resample_anchor.py
git commit -m "fix: session-anchor 1h/4h resampling to 09:15 IST (C-4)"
```

---

# PHASE 3 — Risk, Horizon, Cost

**Exit criteria:** every proposal carries a quantity; correlated clusters cannot exceed their risk budget; expectancy is net of real costs; one horizon governs prompt, measurement, and exits.

---

### Task 3.1: Cost model

**Files:** Create `trading_copilot/config/costs.yaml`, `trading_copilot/core/costs.py`, `tests/test_costs.py`

**Interfaces:** `round_trip_cost_pct(entry: float, exit_px: float, cfg: dict) -> float`; `net_reward(gross: float, entry: float, exit_px: float, spread: float, cfg: dict) -> float`

- [ ] **Step 1: Write the failing test**

```python
from core.costs import round_trip_cost_pct, net_reward, load_costs

def test_round_trip_cost_is_within_the_expected_band():
    """Indian intraday equity: ~0.05-0.10% round trip."""
    c = round_trip_cost_pct(entry=100.0, exit_px=101.0, cfg=load_costs())
    assert 0.03 < c < 0.15, c

def test_cost_scales_with_notional_under_the_brokerage_cap():
    cfg = load_costs()
    assert round_trip_cost_pct(10.0, 10.1, cfg) > round_trip_cost_pct(5000.0, 5050.0, cfg)

def test_net_reward_is_strictly_less_than_gross():
    cfg = load_costs()
    assert net_reward(1.0, 100.0, 101.0, spread=0.05, cfg=cfg) < 1.0
```

- [ ] **Step 2: Run to verify it fails.** Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Author `costs.yaml`** using the rate card from
[improved §4.3.2](../../../improved_architecture_and_features.md#432-cost-model--currently-absent-and-material).
**Verify each rate against your broker's current card before relying on it — these change.**

- [ ] **Step 4: Implement `core/costs.py`** — brokerage (percent, capped), STT on the sell leg only,
exchange transaction charge both legs, SEBI fee, stamp duty on the buy leg, GST on
(brokerage + txn + SEBI). Return the total as a percentage of the entry notional.

- [ ] **Step 5: Wire into `ConvictionScorer`** — replace lines 262–263:

```python
        from core.costs import load_costs, round_trip_cost_pct
        cost_pct = round_trip_cost_pct(calculated_entry, target, load_costs())
        cost_abs = calculated_entry * cost_pct / 100.0
        effective_risk = risk + cost_abs
        effective_reward = max(0.0001, reward - cost_abs)
```

- [ ] **Step 6: Run tests; commit**

```bash
git commit -am "feat: model real round-trip costs in expectancy (improved §4.3.2)"
```

---

### Task 3.2: Cluster map

**Files:** Create `trading_copilot/config/clusters.yaml`, `tests/test_clusters.py`

- [ ] **Step 1: Write the failing test**

```python
from core.risk import load_clusters, cluster_of

def test_adani_entities_share_one_cluster():
    c = load_clusters()
    names = ["ADANIENSOL", "ADANIENT", "ADANIGREEN", "ADANIPORTS"]
    assert len({cluster_of(n, c) for n in names}) == 1

def test_power_theme_is_grouped():
    c = load_clusters()
    assert cluster_of("TATAPOWER", c) == cluster_of("CGPOWER", c)

def test_unmapped_symbol_is_its_own_cluster():
    assert cluster_of("UNKNOWNCO", load_clusters()) == "UNKNOWNCO"
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Author `clusters.yaml`** covering the live watchlist — Adani group (4 names), power &
capital goods (7), PSU metals & infra (5), financials (7), telecom, IT, healthcare, shipping, EV.

- [ ] **Step 4: Implement `cluster_of` and `load_clusters`; run tests; commit.**

---

### Task 3.3: Position sizing and exposure limits

**Files:** Create `trading_copilot/config/risk.yaml`, `trading_copilot/core/risk.py`, `trading_copilot/core/types.py`, `tests/test_risk.py`

**Interfaces:** `RiskLimits`, `Portfolio.risk_in_cluster(cluster) -> float`, `size(proposal, book, limits, adv_shares) -> SizedProposal | Rejection`

Implements [improved §4.3](../../../improved_architecture_and_features.md#43-add-the-risk-layer--the-system-currently-answers-what-but-not-how-much). This is the largest functional gap: the pipeline currently emits entry/stop/target with **no quantity**, and can fire seven correlated LONGs as if independent.

- [ ] **Step 1: Write the failing tests**

```python
from core.risk import size, RiskLimits, Portfolio
from core.types import Proposal

LIM = RiskLimits(capital=1_000_000, risk_per_trade_pct=0.5,
                 max_daily_loss_pct=2.0, max_cluster_risk_pct=1.0,
                 max_adv_participation=0.02)
P = Proposal(symbol="SAIL", bias="LONG", entry=100.0, stop=98.0,
             target=105.0, composite=0.34, regime="TREND_EXPANSION")


def test_quantity_risks_exactly_the_configured_fraction():
    sp = size(P, Portfolio(), LIM, adv_shares=10_000_000)
    assert sp.qty == 2500                       # 5000 risk / 2.0 per share
    assert sp.risk_amount == 5000.0


def test_liquidity_cap_binds_on_thin_names():
    sp = size(P, Portfolio(), LIM, adv_shares=50_000)
    assert sp.qty == 1000                       # 2% of 50,000


def test_cluster_limit_blocks_the_seventh_correlated_signal():
    book = Portfolio()
    book.add_open("ADANIENT", cluster="ADANI", risk_amount=9_500.0)
    p = Proposal(symbol="ADANIGREEN", bias="LONG", entry=100.0, stop=98.0,
                 target=105.0, composite=0.34, regime="TREND_EXPANSION")
    out = size(p, book, LIM, adv_shares=10_000_000)
    assert out.qty <= 250                       # only 500 of headroom remains


def test_daily_loss_limit_rejects_outright():
    book = Portfolio(); book.realized_loss_today = 20_000.0
    out = size(P, book, LIM, adv_shares=10_000_000)
    assert getattr(out, "reason", "") == "DAILY_LOSS_LIMIT"


def test_degenerate_stop_is_rejected():
    p = Proposal(symbol="X", bias="LONG", entry=100.0, stop=100.0, target=105.0,
                 composite=0.3, regime="TREND_EXPANSION")
    assert getattr(size(p, Portfolio(), LIM, 1e7), "reason", "") == "DEGENERATE_STOP"
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement `core/types.py`** — `Proposal`, `Rejection`, `SizedProposal` as frozen dataclasses.

- [ ] **Step 4: Implement `core/risk.py`** per the reference implementation in
[improved §4.3](../../../improved_architecture_and_features.md#43-add-the-risk-layer--the-system-currently-answers-what-but-not-how-much).

- [ ] **Step 5: Insert L3 into the gatekeeper loop** — after `IntradayGatekeeper.evaluate` returns an
authorised proposal, size it, and attach `qty` / `risk_amount` / any rejection to the UI card.

- [ ] **Step 6: Surface quantity in the UI** — add a **Qty** cell beside Entry/Target/Stop in
`renderReasoningReport`, and a cluster-rejection badge.

- [ ] **Step 7: Run tests; commit**

```bash
git commit -am "feat: add L3 risk layer — sizing, liquidity and cluster limits (improved §4.3)"
```

---

### Task 3.4: Unify the horizon

**Files:** Modify `trading_copilot/config/policy_v1.yaml`, `intraday_gatekeeper.py`, `signal_ledger.py`, `performance_analyzer.py`, `templates/index.html:1584`

Fixes [improved §4.4](../../../improved_architecture_and_features.md#44-fix-the-horizon-incoherence): the prompt claims 2–6 h, the ledger grades at 30/60 min, square-off is 15:20, and stagnation aborts at 45 min.

- [ ] **Step 1: Write the failing test**

```python
def test_no_entry_after_the_cutoff(monkeypatch):
    """A signal must not be opened if it cannot run its full horizon."""
    from freezegun import freeze_time
    from intraday_gatekeeper import IntradayGatekeeper as G
    monkeypatch.setattr("pipeline_guard.is_market_open", lambda: True)
    with freeze_time("2026-09-09 14:30:00+05:30"):
        res = G.evaluate({"math_setup": {"setup_rejected": False,
                                         "composite_score": 0.5,
                                         "directional_bias": "LONG",
                                         "expectancy_matrix": {"statistical_edge": 0.3},
                                         "execution_geometry": {}},
                          "market_regime": {"current_regime": "TREND_EXPANSION",
                                            "session_phase": "POWER_HOUR"},
                          "1_live_microstructure": {}}, {}, {}, ltp=100.0)
    assert res["llm_authorized"] is False
    assert "ENTRY_CUTOFF" in res.get("math_rejection", "")
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Enforce `entry_cutoff_ist`** in Path B of the gatekeeper, reading it from `PolicyConfig`.

- [ ] **Step 4: Re-key the ledger to the primary horizon** — replace the hardcoded
`target_30m`/`target_60m` with a loop over `cfg.horizon["measure_at_minutes"]`, producing
`pnl_{n}m_pct` / `directional_correct_{n}m`. `PerformanceAnalyzer` optimises `win_rate_90m`; 30 m stays a
diagnostic.

- [ ] **Step 5: Align `stagnation_abort_minutes`** to 60 (≥ two-thirds of the 90-minute primary).

- [ ] **Step 6: Correct the prompt** — replace "2-6 hour intraday predictive horizon" at
`index.html:1584` and in `reasoning_engine.py`'s default prompt with the configured value.

- [ ] **Step 7: Run tests; commit.**

---

### Task 3.5: Emit tags *and* values; renormalise the composite

**Files:** Modify `trading_copilot/semantic_tagger.py`, `trading_copilot/conviction_scorer.py:141-147`

Implements [improved §4.7](../../../improved_architecture_and_features.md#47-keep-the-semantic-tags--stop-discarding-the-numbers) and fixes [§C-1](../../../drawbacks_false_claimed.md#-c-1-implied_probability-is-a-fabricated-statistic-and-the-catalyst-weight-caps-it).

- [ ] **Step 1: Write the failing tests**

```python
def test_semantic_block_carries_the_scalar_alongside_the_tag():
    from semantic_tagger import SemanticTagger
    out = SemanticTagger.translate_to_llm_payload(
        {"ltp": 100.0, "vol_z_score_5m": 3.4, "obi": 0.71, "atr_1d": 2.0})
    vr = out["1_live_microstructure"]["volume_regime"]
    assert vr["state"] == "TIME_ADJUSTED_SHOCK"
    assert vr["vol_z"] == 3.4

def test_composite_can_reach_one_when_catalyst_is_dead():
    """w_cat multiplies a hardcoded 0.0, capping |composite| at 0.75 in
    TREND_EXPANSION and varying the cap by regime."""
    from conviction_scorer import ConvictionScorer
    s = ConvictionScorer()
    assert s._normalized_weights("TREND_EXPANSION", catalyst_live=False) \
            ["w_micro"] == pytest.approx(0.45 / 0.75)
```

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Change each Block-1/2/3 field** from a bare string to
`{"state": <tag>, <scalar_name>: <value>}`. Keep `.get("state")` accessors working in
`RegimeManager._extract_regime_slice` and `ConvictionScorer` by reading `blk["x"]["state"]`.

- [ ] **Step 4: Add `_normalized_weights`** that renormalises over live components only, and use it in
`score_setup`.

- [ ] **Step 5: Run the full suite** — `SemanticTagger` consumers must all still pass. Commit.

---

# PHASE 4 — Measurement

**Exit criteria:** the system reports only confidence it has measured, and the LLM's contribution is quantifiable.

**Gating:** Task 4.3 is **data-gated** — it cannot be specified in detail before the Phase 1 log exists, and attempting it early would fit noise. Its trigger condition is stated below.

---

### Task 4.1: Shadow-mode both-arm logging

**Files:** Create `trading_copilot/journal/arms.py`; modify `reasoning_engine.py`

Implements [improved §4.5](../../../improved_architecture_and_features.md#45-answer-the-question-the-system-was-built-around-does-the-llm-help).

- [ ] **Step 1: Write the failing test** — assert an `ArmRecord` is written for every escalation, carrying
both the math arm and the LLM arm, and that `ABORT`/`DEFER` verdicts are recorded (today they vanish).

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement `ArmRecord`** (`symbol`, `ts`, `config_version`, `math_arm`, `llm_arm`,
`escalated`) and a JSONL writer under `paths.SIGNALS_DIR / "arms"`.

- [ ] **Step 4: Write one record per escalation**, unconditionally on verdict.

- [ ] **Step 5: Label both arms** with the same `label_outcome` from Task 1.5, so the math-only
counterfactual is scored even when the LLM said ABORT.

- [ ] **Step 6: Add `GET /api/performance/arms`** returning win rate, average R, and count per arm, plus
LLM veto precision (share of ABORTed proposals whose math arm would have lost).

- [ ] **Step 7: Run tests; commit.**

---

### Task 4.2: Report `None` until calibrated

**Files:** Create `trading_copilot/core/calibration.py`; modify `conviction_scorer.py:265-279`, `reasoning_engine.py`, `templates/index.html`

- [ ] **Step 1: Write the failing tests**

```python
def test_implied_probability_is_none_without_calibration():
    from core.calibration import implied_probability
    assert implied_probability(0.34, "TREND_EXPANSION", cal=None) is None

def test_stat_edge_gate_falls_back_to_rr_when_uncalibrated():
    """With no probability, admit on reward:risk alone rather than inventing 64%."""
    from conviction_scorer import ConvictionScorer
    out = ConvictionScorer().score_setup(SEMANTIC_FIXTURE, FLAT_FIXTURE)
    assert out["expectancy_matrix"]["implied_probability"] is None
    assert out["expectancy_matrix"]["reward_risk"] >= 1.5
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement `Calibration`** with `MIN_N = 200`, `.predict()`, `.n_resolved`, and a
`load_calibration()` that returns `None` when `calibration.json` is absent.

- [ ] **Step 4: Replace the invented sigmoid.** Delete the `0.50 + 0.85*(σ(4.5*|composite|) - 0.50)`
expression. Emit `implied_probability: None` and `reward_risk: <ratio>`; gate on
`reward_risk >= cfg.conviction["min_reward_risk"]` while uncalibrated.

- [ ] **Step 5: Update the prompt** — remove the claim that `expectancy_matrix` is ground truth; state
that probability is unmeasured when null.

- [ ] **Step 6: Update the UI** — render `edge: unmeasured (n=<k> < 200)` instead of a confidence number.

- [ ] **Step 7: Run tests; commit.**

---

### Task 4.3: Fit the calibration  🔒 **DATA-GATED**

**Trigger:** ≥200 signals with a resolved primary-horizon outcome in `data/signals/`, **and** ≥20 trading
sessions of `data/features/`. Verify with:

```bash
./.venv/Scripts/python.exe -c "
import pandas as pd, glob
from signal_ledger import SignalLedger
sig = [s for s in SignalLedger.load_all_signals(120)
       if s.get('outcome',{}).get('status','').startswith('RESOLVED')]
days = len(glob.glob('trading_copilot/data/features/date=*'))
print(f'resolved signals: {len(sig)} (need 200) | feature days: {days} (need 20)')
"
```

**Do not start this task before the trigger passes.** Detailed steps are deliberately omitted: the correct
model form depends on the empirical distribution of `composite` versus realised outcome, which does not
exist yet. Writing them now would be fabricated detail.

**When triggered, scope is:** offline script fitting a one-feature logistic on `|composite|` (optionally
per-regime), walk-forward validated, emitting `data/calibration.json` with `b0`, `b1`, `n_resolved`,
`fitted_at`, and decile reliability buckets. `load_calibration()` picks it up; nothing else changes.

**Do not skip to a multi-feature model.** With tens of signals per day it would fit noise —
see [improved §4.2](../../../improved_architecture_and_features.md#42-replace-the-invented-probability-with-a-measured-one--or-with-nothing).

---

### Task 4.4: Reliability view

**Files:** Modify `templates/index.html`; add `GET /api/performance/reliability`

- [ ] **Step 1:** Endpoint returns decile buckets of predicted probability with realised win rate and count.
- [ ] **Step 2:** Render as a table plus a diagonal reference line. A flat curve means the score has no
discriminative power — the view must make that obvious rather than hide it.
- [ ] **Step 3:** Show `n` per bucket; suppress buckets with `n < 10`.
- [ ] **Step 4:** Commit.

---

# PHASE 5 — Operator Surfaces

**Exit criteria:** the operator sees a ranked queue with attribution, live exposure, and can replay a session.

---

### Task 5.1: Attention ranking

**Files:** Modify `reasoning_engine.py`, `templates/index.html`

Implements [improved §4.6](../../../improved_architecture_and_features.md#46-convert-the-gate-into-a-ranked-attention-queue).

- [ ] **Step 1: Write the failing test** — `attention_rank` orders by `ev_r × confidence × freshness`;
a stale proposal ranks below a fresh one with equal EV.
- [ ] **Step 2:** Implement `attention_rank(sp) -> float`.
- [ ] **Step 3:** Sort the Live Action grid by rank; collapse below the top 5.
- [ ] **Step 4: Fix the debounce asymmetry** — `if count < 3: continue` currently suppresses the UI card
as well as the LLM call. Gate escalation only; always publish current state with an `unstable` marker.
- [ ] **Step 5:** Escalate only the top N to the LLM.
- [ ] **Step 6:** Run tests; commit.

---

### Task 5.2: Provenance panel

**Files:** Modify `conviction_scorer.py` (return a `contributions` dict), `templates/index.html`

- [ ] **Step 1:** Have `score_setup` return per-category `{raw, normalized, weight, contribution}` plus the
firing signal names.
- [ ] **Step 2:** Render the decomposition table from
[improved §5.1](../../../improved_architecture_and_features.md#51-signal-provenance-panel), including the
`⚠` marker on the dead catalyst term.
- [ ] **Step 3:** Show `edge` and per-block staleness in the footer.
- [ ] **Step 4:** Commit.

---

### Task 5.3: Exposure view

**Files:** Add `GET /api/risk/exposure`; modify `templates/index.html`

- [ ] **Step 1:** Endpoint returns open risk per cluster against `max_cluster_risk_pct`.
- [ ] **Step 2:** Render as horizontal bars, red at ≥90 % of the limit.
- [ ] **Step 3:** Commit.

---

### Task 5.4: Replay runner

**Files:** Create `trading_copilot/replay/runner.py`, `tests/test_replay.py`

Implements [improved §5.4](../../../improved_architecture_and_features.md#54-replay--paper-mode). Depends on Tasks 1.1, 2.1, 2.2 (pure `evaluate`).

- [ ] **Step 1: Write the failing test** — replaying a recorded tick file twice with the same config
produces byte-identical proposal sequences (determinism), and a changed threshold produces a different one.
- [ ] **Step 2:** Implement `replay(tick_files, cfg, speed=0) -> list[FeatureRecord]` feeding recorded ticks
through the same `process_tick` → `build_features` → `evaluate` path with `mode="replay"`.
- [ ] **Step 3:** Add a CLI: `python -m replay.runner --date 2026-09-09 --config config/policy_v2.yaml`.
- [ ] **Step 4:** Run tests; commit.

---

### Task 5.5: Fix the screener and close the discovery loop

**Files:** Modify `screener_engine.py:36-40,48-55,87-88`; add `config/watchlist_policy.yaml`

Fixes [§A-15](../../../drawbacks_false_claimed.md#-a-15-screeners-volume-shock-term-is-a-constant-for-most-stocks) and implements [improved §4.9](../../../improved_architecture_and_features.md#49-close-the-discovery-loop).

- [ ] **Step 1: Write the failing test** — a stock at exactly average volume scores 0 on the shock term,
and a 3× volume day scores full marks.
- [ ] **Step 2:** Replace `min((live/ma)*35, 35)` with `min(max(0, ratio-1)*17.5, 35)`.
- [ ] **Step 3:** Normalise the three polarity contributions to a common z-scale.
- [ ] **Step 4:** Rename `prev_day_high`/`prev_day_low` to `latest_bar_high`/`_low`, or index `iloc[-2]`.
- [ ] **Step 5:** Implement the bounded watchlist loop with `core`, `dynamic_slots`, `cluster_cap`,
`min_adv_crore`, `churn_cap`.
- [ ] **Step 6:** Run tests; commit.

---

### Task 5.6: Session review and dead-code removal

- [ ] **Step 1:** Add `GET /api/session/review` — signals, outcomes at the primary horizon, best/worst by
cluster and regime, config version, staleness incidents.
- [ ] **Step 2:** Render as an end-of-day panel.
- [ ] **Step 3: Salvage then delete.** Port `macro_eod_engine.fetch_market_breadth` (the *correct* NIFTY-50
A/D implementation) into `macro_worker`, then delete `smart_api_feed.py`, `websocket_engine.py`,
`auth_manager.py`, `stitching_engine.py`, `warm_layer_engine.py`, `macro_eod_engine.py` (~714 lines).
- [ ] **Step 4:** Relabel the UI's "Market Breadth" to reflect its real source.
- [ ] **Step 5:** Run the full suite; commit.

---

## Self-Review — Coverage Matrix

Every finding in the two source documents mapped to a task. Gaps are listed explicitly.

### Drawbacks §A — Critical correctness

| ID | Finding | Task |
|---|---|---|
| A-1 | Fabricated macro narrative | 0.6 |
| A-2 | Phantom volume accumulates cumulative VTT | 0.3 |
| A-3 | Session reset inconsistency + anomaly guard | 0.3, 0.4 |
| A-4 | Display path mutates decision state | 2.2 |
| A-5 | Two regimes unreachable | 2.3 |
| A-6 | Whale-CVD force-close | 0.9 |
| A-7 | Playbook blocks the event loop | **Gap — see below** |
| A-8 | `record_signal` null-geometry crash | 0.5 |
| A-9 | Two dead data paths | 0.2 |
| A-10 | Unvalidated LLM prices | 0.10 |
| A-11 | Missing alert endpoints | 0.7 |
| A-12 | No stream reconnect | 2.5 |
| A-13 | Per-symbol failure starves the batch | 0.8 |
| A-14 | Parquet duplicate rows | **Gap — see below** |
| A-15 | Screener volume-shock saturation | 5.5 |

### Drawbacks §B–§E

| ID | Finding | Task |
|---|---|---|
| B-21 | README `.env` keys wrong | 0.11 |
| B-25 | Performance endpoints have no UI | 4.4, 5.3, 5.6 |
| B-26 | `interval` accepted and ignored | 5.1 |
| B-28 | Dead map-option-tokens button | 0.7 |
| B-29 | `start_all.bat` broken | 0.11 |
| C-1 | Invented probability + dead catalyst weight | 3.5, 4.2 |
| C-2 | Wrong 5-year Value Area algorithm | **Gap — see below** |
| C-3 | Biased sample, trivial win criterion, lost pendings | 1.5 |
| C-4 | Unsound indicators (anchoring, whale slope, POC bins) | 2.7, 3.5 |
| D-1 | ~100 ledger scans/sec | 2.6 |
| D-2 | Disk I/O in the hot loop | 2.6 |
| D-3 | Full recomputation for one value | 2.6 |
| D-4 | Blocking work on the event loop | 0.8, 2.6 |
| D-5 | Cross-process polling transport | **Deferred — see below** |
| D-6 | Client construction, deprecated APIs | 2.6, 0.11 |
| E-1..E-5 | Bind, CORS, TOTP seed, XSS, `.env` | 0.11, 0.6 |
| E-7 | No tests | 0.1 and every task thereafter |
| E-8 | Repo hygiene | 0.11 |
| F | Dead code | 5.6 |

### Improved architecture §3–§5

| § | Change | Task |
|---|---|---|
| 3.1 | Pure L1/L2 | 2.2, 5.4 |
| 3.2 | Config externalisation | 2.1 |
| 4.1 | Tick + feature recording | 1.1–1.4 |
| 4.2 | Measured probability | 4.2, 4.3 |
| 4.3 | Risk layer | 3.1–3.3 |
| 4.4 | Horizon coherence | 3.4 |
| 4.5 | Shadow mode | 4.1 |
| 4.6 | Attention ranking | 5.1 |
| 4.7 | Tags + values | 3.5 |
| 4.8 | Staleness first-class | 2.5 |
| 4.9 | Discovery loop | 5.5 |
| 4.10 | Single-writer ingest | 2.4 |
| 5.1–5.5 | Operator surfaces | 5.2, 4.4, 5.3, 5.4, 5.6 |

### Deliberate gaps

Three findings have **no task**, by decision rather than oversight:

1. **A-7 (playbook blocks the event loop).** The clean fix is a `GET /api/token` on port 8001 so process 4
   never authenticates. But `generate_intraday_playbook` also duplicates the whole-market screener from the
   wrong process and enriches against a key space that never matches. **Recommendation: disable the
   Discovery button until Task 5.5 rebuilds the loop properly**, rather than patch a feature that is
   getting rewritten. Add to Task 5.5 as its first step.
2. **A-14 (parquet duplicate rows).** Verified `dupes: 0` on the current files — the defect is in the code
   path, not yet in the data. The two-line fix belongs in whichever task next touches
   `parquet_engine.py`; if none does, do it standalone. Low risk, low urgency.
3. **C-2 (wrong 5-year Value Area).** `structural_liquidity_5y` levels reach the payload but
   `SemanticTagger` uses the *correct* `rolling_20d_*` levels for proximity, so nothing acts on them today.
   The fix — call the existing correct `calc_volume_profile_high_fidelity` from `macro_bootstrap` and
   regenerate baselines — is worth doing **before** they are ever wired into a decision.

### Deferred by design

**D-5 (cross-process transport).** Replacing 2 Hz full-state polling with server-sent events is a real
improvement, but Task 2.2 removes the expensive part (the semantic pipeline no longer runs at 2 Hz), which
takes most of the cost out. Re-measure after Phase 2 and only then decide whether the transport still
warrants changing.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-09-quantflow-remediation-and-measurability.md`. Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
