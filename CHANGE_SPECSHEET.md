# QuantFlow Change Specsheet

Running record of every change made during implementation: what changed, why, how it was verified,
and anything a future reader needs to know. Updated as each task lands.

**Plan:** [docs/superpowers/plans/2026-09-09-quantflow-remediation-and-measurability.md](docs/superpowers/plans/2026-09-09-quantflow-remediation-and-measurability.md)
**Tasks:** [IMPLEMENTATION_TASKS.md](IMPLEMENTATION_TASKS.md)
**Branch:** `feat/measurability` (base: `master` @ `011eec0`)

---

## Environment baseline (measured at start)

| Item | Value |
|---|---|
| Python | 3.12.10 |
| pandas | 3.0.3 — **copy-on-write is default** |
| numpy | 2.2.6 |
| pyarrow | 24.0.0 |
| TA-Lib | 0.6.8 |
| fastapi | 0.136.1 |
| google-genai | 2.6.0 |
| pytest / PyYAML / freezegun | **absent at start** — installed in Task 0.1 |
| Existing tests | none |

### Workspace decision

The plan and the `executing-plans` skill both default to a git worktree. **Not used here**, deliberately:
`trading_copilot/data/` (all parquet files, `macro_baselines.json`) and `.env` are **untracked**, so a
worktree checkout would omit them and Task 0.2's verification would fail for the wrong reason.
Work proceeds on branch `feat/measurability` in the existing working tree; tracked changes are isolated,
untracked runtime data stays in place.

---

## Conventions adopted

- All filesystem paths come from `trading_copilot/paths.py`. No new `os.path.dirname(__file__)` chains.
- Timezone-aware IST via `zoneinfo.ZoneInfo("Asia/Kolkata")`. No `datetime.utcnow()`, no hand-added offsets.
- Unknown values propagate as `None` and render as unknown. Never substitute a plausible-looking default.
- Every behavioural change lands with a test that failed before it and passes after.

---

## Phase 0 — Foundation & P0 Correctness

### Task 0.1 — Test infrastructure and branch
**Commit:** `46586d8`

| | |
|---|---|
| Files created | `tests/conftest.py`, `tests/test_smoke.py`, `pytest.ini` |
| Files modified | `requirements.txt` |

`requirements.txt` had its last two entries (`upstox-python`, `playwright`) appended as UTF-16LE bytes
into a UTF-8 file — `pip install -r requirements.txt` would have resolved garbage package names. Verified
at the byte level before and after. Rewritten as clean UTF-8, and pruned of the dead Angel One stack
(`smartapi-python`, `logzero`, `websocket-client`, `rich`) which back only disabled/dead code paths, plus
test tooling (`pytest`, `pytest-asyncio`, `PyYAML`, `freezegun`, `httpx`) added for this branch.

`tests/conftest.py` puts both the repo root and `trading_copilot/` on `sys.path`, so existing flat imports
(`from technical_engine import MathEngine`) and new `core.*`/`journal.*` packages both resolve.

**Verified:** `pytest tests/test_smoke.py -v` → 1 passed.

---

### Task 0.2 — Centralise path resolution
**Commit:** `7e239ca` | **Fixes:** drawbacks §A-9

| | |
|---|---|
| Files created | `trading_copilot/paths.py`, `tests/test_paths.py` |
| Files modified | `rolling_state_engine.py`, `derivatives_worker.py`, `signal_ledger.py`, `history_manager.py`, `api_server.py`, `reasoning_engine.py`, `scrip_master_engine.py`, `data_services/parquet_engine.py`, `data_services/macro_worker.py`, `data_services/news_feed.py`, `data_services/upstox_feed.py` |

**What was broken:** two `os.path.dirname(os.path.dirname(os.path.abspath(__file__)))` chains resolved to
`AlgoTrade/data` — a directory that does not exist (the real data lives at `AlgoTrade/trading_copilot/data`):

- `RollingStateEngine._load_parquet_metrics()` — silently loaded nothing for all 27 watchlist symbols,
  because the `os.path.exists()` guard just failed.
- `derivatives_worker.get_historical_iv()` — always returned `(999.0, 0.0)`, silently degrading IV Rank
  onto its `macro_baselines.json` fallback path instead of reading the 252-day parquet series it was
  designed to use.

Both failures were silent — no exception, no log line — because they were guarded by `os.path.exists()`
checks that simply returned `False`.

**Fix:** `trading_copilot/paths.py` is now the single source of truth (`DATA_DIR`, `SIGNALS_DIR`,
`TICKS_DIR`, `FEATURES_DIR`, `CACHE_DIR`, `CONFIG_DIR`, `WATCHLIST_PATH`, `TOKEN_PATH`, `PLAYBOOK_PATH`,
`MACRO_BASELINES_PATH`, `INSTITUTIONAL_FLOW_PATH`, `CACHE_STATE_PATH`, `TRADE_HISTORY_PATH`,
`SCRIP_MASTER_PATH`, plus `parquet_path(symbol)` and `ensure_dirs()`). Every call site migrated; only
`sys.path.append(...)` bootstrap lines remain using the old pattern (they must run before `paths` itself
is importable, so that's correct).

Also fixed in passing: the CWD-relative `playbook_state.json` literal (`os.path.join("trading_copilot",
"playbook_state.json")` — worked only when launched from the repo root, silently broke under
`start_all.bat`'s `-d .`), and the derivatives debug dump moved from a CWD-relative `scratch/` to
`CACHE_DIR`.

**Verified (executed, not inferred):**
```
get_historical_iv('RELIANCE') -> (9.54, 72.13)      # was unconditionally (999.0, 0.0)
```
`pytest tests/test_paths.py` → 4 passed. Full suite green. All 20 touched modules confirmed importable.

**Note for later phases:** `ensure_dirs()` is now called eagerly on import of `api_server.py`,
`upstox_feed.py`, `parquet_engine.py`, and `macro_worker.py` — each of Phase 1's new writers
(`TICKS_DIR`, `FEATURES_DIR`) can rely on their parent directories already existing.

---

### Task 0.3 — Correct VTT volume accounting
**Commit:** `35acf4f` | **Fixes:** drawbacks §A-2, and the anomaly-guard half of §A-3

| | |
|---|---|
| Files modified | `microstructure_engine.py`, `rolling_state_engine.py` |
| Test | `tests/test_volume_accounting.py` (6 tests) |

**What was broken:** Upstox `vtt` is a *cumulative* "volume traded today" counter.
`RollingStateEngine.process_tick` treated it as a per-tick delta — `phantom['volume'] += volume` added the
running cumulative total on every tick. Measured effect: a bar whose true 5-minute volume was 4,000 shares
was recorded as 2,004,000. That corrupted phantom was then committed into `ltf_df` on every 5-minute
boundary, permanently poisoning the historical time-of-day volume baseline that `vol_z_score` and
`TIME_ADJUSTED_SHOCK` are measured against — not a display bug, a data-corruption bug.

Separately, the existing anomaly guard (`if prev_vol > 100000 and tick_vol > prev_vol * 0.5: tick_vol = 0`)
discarded any increment exceeding half of cumulative-volume-so-far. That condition is routinely true in the
first ~30 minutes of a session (`OPENING_RANGE`), so it silently destroyed genuine volume spikes —
precisely the ones `TIME_ADJUSTED_SHOCK` exists to detect. Reproduced directly: feeding a normal
1,000,000 → 3,000,000 → 5,000,000 cumulative sequence fired the guard twice, discarding 4,000,000 shares.

**Fix:**
- `MicrostructureEngine.generate_microstructure_payload` now owns VTT differencing in exactly one place
  and publishes the result as a new `tick_volume` key. Guard logic changed from "increment too large →
  discard" to "counter went backwards → treat as a new baseline" (the real signature of a reconnect or
  session rollover).
- `RollingStateEngine.process_tick` restructured so `MicrostructureEngine` runs *before* the phantom-candle
  update, and the phantom consumes `micro_state['tick_volume']` instead of the raw cumulative `volume`
  parameter. The two subsystems can no longer disagree about what "volume" means.

**Verified:** `test_committed_bar_carries_realistic_volume` asserts the 5-minute commit carries `4_000.0`,
not `2_004_000.0`. All 6 tests pass; full suite green (11 tests total at this point).

---

### Task 0.4 — Single session-boundary reset
**Commit:** `e617706` | **Fixes:** drawbacks §A-3 (the second finding — carried-over state)

| | |
|---|---|
| Files modified | `microstructure_engine.py` |
| Test | `tests/test_session_reset.py` (4 tests) |

**What was broken:** only two of seven per-token accumulators (`cvd_state`, `whale_cvd_state`) were reset
on a date change, and that reset was inline inside `update_session_vwap`. `vol_profile_state` and
`whale_cvd_history` were never cleared, so the "intraday" POC was really an all-time-since-process-start
POC, and the whale-CVD EMA/slope mixed yesterday's flow into today's reading. `last_vtt_state` and
`last_bba_state` were also outside the reset (self-correcting after one tick regardless — see the note in
`drawbacks_false_claimed.md` §A-3, corrected during test-writing for this task).

**Fix:** all seven per-token dicts (`cvd_state`, `vol_profile_state`, `session_vwap_state`,
`whale_cvd_state`, `whale_cvd_history`, `last_vtt_state`, `last_bba_state`) are now declared as explicit
class attributes (previously two of them were created lazily via `hasattr()` checks — which is how they
came to be forgotten). A new `MicrostructureEngine.roll_session_if_needed(token)` clears all seven together
when the IST calendar date changes, called once at the top of `generate_microstructure_payload` before any
per-tick logic runs. `update_session_vwap` no longer performs its own reset — it only accumulates, and
keeps `last_reset_date` purely as a diagnostic field.

**Verified:** `test_poc_does_not_carry_yesterdays_prices` — POC computed the day after a session at price
500 correctly returns `100`, not `500`. All 4 tests pass; full suite green (15 tests total at this point).

---

### Unplanned: security incident

**Commits:** `27b1c93` (fix) — surfaced between Tasks 0.4 and 0.5

**What happened:** Task 0.2's `git add -A trading_copilot/` re-tracked `trading_copilot/upstox_token.json`,
which contains a live Upstox JWT access token. The file had been untracked from git history at an earlier
commit (`93b4906`, predating this branch) but was never added to `.gitignore`, so once it existed on disk
again with a freshly-issued token, the blanket `add -A` picked it up. It was committed as a new file inside
`7e239ca`.

**Investigated and confirmed:**
- `feat/measurability` (this branch) has **never been pushed** to `origin` — the leaked token in `7e239ca`
  has not left this machine.
- A **separate, pre-existing** issue unrelated to this session: an older token was committed to `master` at
  `2c38035`, and that commit **is** reachable from `origin/master`. If the GitHub repo
  (`MutantCoder123/QuantFlow`) is public, that older credential has already been exposed. This predates
  Task 0.2 by a wide margin and was discovered only while investigating the new incident.
- `.env` has never been tracked in git history, at any point. No other tracked file was found to contain a
  literal secret value (only source-code references to variable/env-var names like `access_token =
  data.get(...)`).

**Fix applied (non-destructive, safe):**
- `git rm --cached trading_copilot/upstox_token.json` — untracked, file kept on disk.
- `.gitignore` updated with a `# Secrets / credentials` section covering `upstox_token.json` at both
  locations.
- (Considered and rejected: adding `fii_dii_state.json` to the same gitignore block — checked its content
  first; it's derived market data (`{"fii_net": ..., "dii_net": ..., "date": ..., "ad_ratio": ...}`), not a
  secret. Left untouched rather than mislabelling it.)

**Not done, pending user decision:**
- Rewriting the local `7e239ca` commit to strip the secret before it could ever be pushed. Not done because
  history rewriting requires explicit user consent per operating rules, even for a commit that was never
  pushed.
- Anything touching `master` or `origin` regarding the pre-existing `2c38035` exposure — flagged only.

**Open question for the user** (asked, not yet answered as of this writing):
1. Rotate/invalidate the Upstox token via re-authentication (recommended regardless of push status).
2. Whether to rewrite the local `7e239ca` commit to remove the secret from this branch's history.
3. Whether/how to address the older, already-potentially-public `2c38035` exposure on `master`/`origin`.

---

## Open questions / follow-ups

- **Security incident above** — awaiting user decision on token rotation and history rewriting (both the
  local `7e239ca` commit and the pre-existing `master`/`origin` exposure at `2c38035`).
