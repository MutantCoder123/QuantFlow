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

### Task 0.5 — Fix SignalLedger crash on held positions
**Commit:** `1954274` | **Fixes:** drawbacks §A-8

| | |
|---|---|
| Files modified | `signal_ledger.py` |
| Test | `tests/test_signal_ledger.py` (2 tests) |

**What was broken:** `build_structured_payload()` deliberately sets `execution_geometry` and
`expectancy_matrix` to `None` (not absent) while a position is held, to hide new-entry suggestions during
an active trade. `math_setup.get("execution_geometry", {})` returns the *stored* `None` in that case — the
default only applies when the key is missing — so the chained `.get("padded_stop")` raised
`AttributeError: 'NoneType' object has no attribute 'get'`. Reproduced directly before the fix.

Because `record_signal()` is only reached on `CLOSE_EXISTING`/`REVERSE_POSITION` verdicts — which by
definition require an existing position — this meant **every position-management signal was silently
dropped**, and the exception was swallowed by `analyze_stock`'s outer handler, surfacing to the operator
only as a generic `"Error generating report: ..."`.

**Fix:** `geo = math_setup.get("execution_geometry") or {}` / `exp = math_setup.get("expectancy_matrix") or
{}`, computed once before building the record. Also changed `implied_probability`'s default from `0.0` to
`None` — consistent with `improved_architecture_and_features.md` §4.2 (unknown should read as unknown, not
as a specific wrong number).

**Verified:** both tests pass — one exercising the previously-crashing null-geometry path, one confirming
real geometry is still captured correctly (regression guard against over-correcting to "always empty").

---

### Task 0.6 — Remove the fabricated macro narrative
**Commit:** `5492c2c` | **Fixes:** drawbacks §A-1

| | |
|---|---|
| Files modified | `trading_copilot/templates/index.html` |

**What was broken:** whenever `payload.global_market_context` was falsy — `news_feed.py` not running,
`GEMINI_API_KEY` missing, the Upstox news call failing, or simply the first ~30 minutes of every session
before `_macro_news_polling_loop` completes its first cycle — the WebSocket handler substituted a hardcoded
object: a fixed BULLISH narrative ("Global markets are showing strong resilience...") stamped with
`Math.floor(Date.now() / 1000)`, i.e. the *current* time. Rendered with a green sentiment badge and an
"Updated: <now>" label, it was indistinguishable from a genuine Gemini-generated read. Every session
therefore began by showing the operator a fabricated, systematically bullish directional thesis.

**Fix:** deleted the mock-substitution block entirely. The render logic now branches explicitly: when
`ctx` is absent, the badge reads `UNAVAILABLE` (slate/grey), the summary states the specific reason
("Macro context unavailable — news_feed (:8003) has not reported yet."), and no timestamp is shown. Also
switched `innerText` → `textContent` for these three fields, closing the incidental XSS sink on
LLM/news-derived text in this one panel (broader `innerHTML` cleanup across the rest of the file is
tracked separately in the plan, not attempted here).

**Verified:**
- Grep sweep confirms no fabricated-data pattern (`mock`, `fabricat`, `hardcode`) remains in the file,
  aside from the two explanatory code comments describing the fix itself.
- Static content check: the fabricated narrative string is gone; the `UNAVAILABLE` badge text is present.
- All 4 inline `<script>` blocks in `index.html` (including the modified 46,056-character block) pass
  `node --check` — genuine JS syntax verification via Node 22.14.0, not visual inspection.
- **Not performed:** an actual browser load (starting `main.py` without `news_feed.py` and observing the
  rendered panel). No browser automation tool is available in this environment (Playwright MCP was
  disconnected at session start). This is disclosed rather than claimed.

---

### Task 0.7 — Expose alert endpoints
**Commit:** `711dc25` | **Fixes:** drawbacks §A-11, §B-28

| | |
|---|---|
| Files modified | `api_server.py`, `templates/index.html` |
| Test | `tests/test_alert_endpoints.py` (5 tests) |

**What was broken:** the UI polled `GET /api/alerts/unread`, `GET /api/alerts/history`, and
`POST /api/alerts/mark-read/{id}` — none of which existed on the server (verified: `api_server.py` had 28
routes, none matching). `ReasoningEngine.global_alerts` was faithfully populated on every actionable
verdict (capped at 50) but never exposed. The failure was silent: FastAPI's 404 → `data.count` undefined →
`undefined > 0` is `false` → badge stays hidden. The operator saw a permanently empty alert tray and
reasonably concluded there were no alerts.

Separately, `/api/map-option-tokens` proxied to port 8001, but that route only ever existed on the dead
Angel One `smart_api_feed.py` — the live `upstox_feed.py` never defined it, so the call always errored.

**Fix:** added the three alert routes (`unread` sums unread flags; `history` returns the list; `mark-read`
flips the flag for a matching id, no-op on unknown id). Removed `/api/map-option-tokens` and its "Map
Option Tokens" button + click handler from `index.html` rather than fixing it, since the feature it
targeted no longer exists.

**Verified:** all 5 tests pass, including one confirming `mark-read` on an unknown id returns success
rather than erroring, and one confirming `/api/map-option-tokens` now correctly 404s. All 4 inline
`<script>` blocks in `index.html` still pass `node --check` after the removal; grep confirms zero
remaining references to `btn-map-tokens` or `map-option-tokens`.

---

### Task 0.8 — Isolate per-symbol failures in the calculation loop
**Commit:** `1bdcac8` | **Fixes:** drawbacks §A-13, §D-4

| | |
|---|---|
| Files modified | `rolling_state_engine.py` |
| Test | `tests/test_loop_isolation.py` (3 tests) |

**What was broken:** `calculate_technicals_loop`'s single `try` opened immediately after `while True:` and
its `except` closed immediately before the trailing `await asyncio.sleep(1.5)` — wrapping the **entire**
per-cycle `for` loop over all 27 symbols. Only the `MathEngine.generate_signal_payload` call had its own
narrower inner try/except (which already correctly `continue`d past a MathEngine failure on just that
symbol). The ~130 lines *after* that inner block — building the remaining `final_payload` fields (macro
baselines, FII/DII, microstructure, market breadth, news, confluence checks) and calling
`TerminalDashboard.update_state` — had **no protection at all**. An exception anywhere in that stretch
propagated to the outer `except`, aborting every symbol after the failing one for that entire 1.5-second
cycle. A deterministic fault (bad data for one symbol) would starve the rest indefinitely while the log
showed one repeating line.

**Test-writing correction (documented for the record):** the first draft of the test injected the fault
into `MathEngine.generate_signal_payload` — which the test itself proved was the *wrong* location: it
passed even before any fix existed, because that call was already isolated by its own inner
try/except. Rewrote the fault injection to target `TerminalDashboard.update_state` (in the previously
*unprotected* stretch), which correctly reproduced the real defect — confirmed by a `git stash` round trip
showing the corrected tests fail with `AttributeError: no run_one_cycle` against the pre-fix code and pass
after.

**Fix:** split the method into three:
- `calculate_technicals_loop` — thin driver, unchanged public entry point, calls `run_one_cycle()` then
  sleeps.
- `run_one_cycle` — iterates all symbols with a **per-symbol** try/except; tracks consecutive failures per
  token in `self._failures` (initialised in `__init__`), logging only at the 1st/10th/100th occurrence
  rather than every 1.5 s; clears the counter on success; yields `await asyncio.sleep(0)` between symbols
  so the `/state` endpoint is not starved for an entire batch (D-4).
- `_compute_symbol(self, token, phantom)` — the moved per-symbol body, logic unchanged, mechanically
  dedented and with its three loop-scoped `continue` statements converted to `return` (verified: an
  `assert "continue" not in dedented_src` in the transformation script would have caught any missed one —
  none were).

**Verified:** all 3 tests pass; full suite green (24 tests total at this point).

---

### Task 0.9 — Correct whale-CVD polarity check
**Commits:** `36ef5e9` (core fix), `24b5528` (wiring) | **Fixes:** drawbacks §A-6

| | |
|---|---|
| Files modified | `intraday_gatekeeper.py`, `api_server.py`, `rolling_state_engine.py` |
| Test | `tests/test_gatekeeper_polarity.py` (5 tests), `tests/test_position_whale_baseline.py` (3 tests), `tests/test_adv_shares.py` (2 tests) |

**What was broken:** the active-position whipsaw guard tested the *sign* of the cumulative
`whale_cvd_ema_1h` level (`if direction == "Long" and whale_cvd_ema_1h < 0`), despite its own comment
saying it should detect a *polarity flip*. `whale_cvd_ema_1h` is a signed cumulative share count; for any
symbol with net large-print selling since session open it is negative essentially all day, independent of
price action. Reproduced directly: a long position with `whale_cvd_ema_1h = -480,000` (barely different
from its `-500,000` entry baseline) forced a `Close` at confidence 9/10, burning an LLM call for a value
that had barely moved.

**Fix (commit `36ef5e9`):** replaced the sign test with the *change* in whale flow since entry
(`(whale_cvd_ema_1h - whale_cvd_at_entry) / adv_shares`), gated at 3% of average daily volume moved
against the position — a threshold comparable across a ₹20 stock and a ₹10,000 one, unlike a fixed absolute
share count.

**Test-writing correction (documented for the record):** the first test run failed for a reason unrelated
to the fix — `IntradayGatekeeper.evaluate`'s forced 15:20 IST square-off computes its own wall-clock time
independently of the mocked `pipeline_guard.is_market_open()`, and real elapsed time during this session
had passed 15:20 IST (started ~14:30, by this task real time was 19:35). Every test returned `Close` from
that unrelated branch. Fixed by freezing time via `freezegun` in the autouse fixture rather than depending
on whatever time the suite happens to run at — a fragility worth knowing about for any future test that
exercises `intraday_gatekeeper`.

**Gap found and closed (commit `24b5528`):** the core fix reads `position.whale_cvd_at_entry` and
`raw_payload.adv_shares`, but nothing populated either — every position would have silently compared
against a `0.0` baseline and every normalisation would have hit its safe floor, making the fix inert in
practice despite passing its own unit tests (which supply both values directly). Closed by:
- `save_position_api` now backfills `whale_cvd_at_entry` from the symbol's live `whale_cvd_ema_1h` when the
  caller doesn't supply one; an explicit caller-supplied value is never overwritten.
- `_compute_symbol` now publishes `adv_shares` as a genuine 20-session average volume.

**Deliberate deviation from the plan's literal pseudocode:** the plan's Step 5 guard was
`if ltf is not None and len(ltf) > 75:` with the comment "20 sessions x 75 five-minute bars" — but `> 75`
bars is only *3* sessions' worth, not 20; the guard didn't match its own comment. Implemented
`len(target_df) >= 1500` instead (75 × 20 = 1500), so `adv_shares` is either a genuine 20-session average or
explicitly `0.0` — never a partial-window figure silently mislabelled as a full ADV. A test
(`test_adv_shares_defaults_to_zero_with_insufficient_history`) locks in this behaviour.

**Verified:** all 10 new tests pass; full suite green (35 tests total at this point, up from 24 before this
task).

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
