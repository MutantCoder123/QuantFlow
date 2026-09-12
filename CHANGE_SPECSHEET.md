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

### Task 0.10 — Clamp LLM-returned prices server-side (fixes A-10)

**Files:** `trading_copilot/reasoning_engine.py`, `tests/test_ticket_clamp.py` (new). Commit `d2a87c2`.

**Problem:** the prompt tells the LLM "you may ONLY modify risk_parameters within these bounds," but
nothing server-side enforced that. A hallucinated `final_entry`/`final_stop`/`final_target` reached the UI
as an actionable price with no validation, indistinguishable from a value derived from real math.

**Fix:** new module-level `clamp_risk_parameters(risk_params, geo, atr15, bias) -> (dict, error|None)`:
- Entry clamped to ±0.3% of `geo["calculated_entry"]`.
- Stop clamped to the band `[padded_stop - 1.0*ATR15, padded_stop + 0.5*ATR15]`.
- Target bounded so it can only move the "generous" direction by up to 0.5*ATR15 from
  `geo["calculated_target"]`.
- If the resulting geometry is internally inverted for the position's bias, the whole ticket falls back to
  the deterministic math geometry unchanged and returns `error="LLM_GEOMETRY_REJECTED"`.
- Empty/missing `risk_params` (e.g. a HOLD/CLOSE ticket) returns the fallback with no error.

Wired into `ReasoningEngine.analyze_stock`: `ui_data` is now built from `clamp_risk_parameters(...)` output
rather than the raw `ticket.get("risk_parameters")`, and a new `geometry_override` field carries the
rejection reason (or `None`) so the UI can flag when the LLM's numbers were discarded.

**Deliberate deviation from the plan's literal pseudocode (same category of issue as Task 0.9's `> 75` vs
`>= 1500`):** the plan checks `ordered` against the *already-clamped* stop and target. Traced through the
fixture values (`padded_stop=98`, `calculated_entry=100`, `atr15=1.0`): the stop's clamp band is
`[97, 98.5]`, entirely below the entry's clamp band `[99.7, 100.3]`, so `stop < entry < target` holds
*unconditionally* after clamping regardless of what the LLM originally sent — the rejection branch is
dead code under the plan's literal ordering. Concretely, `test_inverted_geometry_is_rejected_and_falls_back`
(final_stop=106, i.e. a stop hallucinated above the entry, nonsensical for a long) fails under the literal
pseudocode: 106 clamps down into the valid-looking `98.5`, and `98.5 < 100 < 105` reads as ordered, so the
hallucinated inversion would silently pass through as a plausible price. Fixed by checking `ordered` against
the **raw** stop/target (before their own clamp) against the *already-bounded* entry — this is what actually
distinguishes "entry needed reining in" (test 1, not rejected) from "stop is on the wrong side of the
position entirely" (test 3, rejected). Traced against all four tests in the specsheet entry above before
implementing; all four pass under this ordering and only this ordering.

**Verified:** 4/4 new tests pass; full suite green (39 tests total).

---

### Task 0.11 — Security and documentation hygiene (fixes E-1, E-2, E-3, B-21, B-29)

**Files:** `.env.example`, `README.md`, `.gitignore`, `trading_copilot/api_server.py`,
`trading_copilot/start_all.bat`. Commit `7845524`.

- **`.env.example`:** `UPSTOX_TOTP_KEY` held a real-looking base32 value (`XHRGR2YOVCEGBYT3MQEBLI6CV3POJD7L`)
  while every other entry was a `your_*` placeholder — almost certainly a real seed pasted into the wrong
  file. Confirmed this file is a template only, never read at runtime (the app reads the separate,
  already-untracked `.env`), so replacing it does not affect the running system. Replaced with
  `your_totp_secret` and dropped the dead `ANGEL_*` block (Angel One is retired code per
  `drawbacks_false_claimed.md` §F). **Flagged to the user in the same turn: rotate this in Upstox if it is a
  live seed** — it predates this session and its exposure history outside git is unknown.
- **`README.md`:** documented `UPSTOX_API_KEY`/`UPSTOX_API_SECRET`, but the code
  (`data_services/upstox_feed.py:48-53`, confirmed by direct read) reads `UPSTOX_CLIENT_ID`/
  `UPSTOX_CLIENT_SECRET`/`UPSTOX_MOBILE_NO`. Following the README as written yields `client_id=None`.
  Replaced the `.env` block with the keys the code actually reads.
- **`api_server.py` CORS:** `allow_origins=["*"]` combined with `allow_credentials=True` against an
  unauthenticated API meant any page open in the same browser could call it cross-origin. Scoped to
  `http://127.0.0.1:8000` and `http://localhost:8000`, and `allow_methods` narrowed from `["*"]` to the two
  methods the API actually uses (`GET`, `POST`).
- **`api_server.py` bind address:** `uvicorn.Config(..., host="0.0.0.0", ...)` exposed the unauthenticated
  API to the whole LAN. Bound to `127.0.0.1`; left a comment documenting that LAN access needs a
  shared-secret header dependency first, not just reopening the bind.
- **`start_all.bat`:** launched `smart_api_feed.py` (dead Angel One stack) and `nse_feed.py` (does not exist
  anywhere in the repo — verified via `ls`), and its `-d .` set CWD to `trading_copilot/`, which broke the
  playbook path before Task 0.2 centralised path resolution. Replaced with the four services that actually
  run today (`upstox_feed.py`, `news_feed.py`, `macro_worker.py`, `main.py`), launched from the repo root —
  all four target paths verified to exist before committing.
- **`.gitignore`:** added the scratch/debug-artefact patterns accumulated in the repo root during this
  session (`scratch/`, `temp.js`, `scratch_script_*.js`, `script_test_*.js`, root-level `test_*.py`,
  `grep_transcript.txt`, `current_diff.txt`, `*.tex`, `output.json`, `response.json`, `index_edits.txt`,
  `tab_html3.txt`), plus an explicit `!tests/test_*.py` negation. Verified with
  `git check-ignore -v tests/test_smoke.py test_gk.py`: the tracked test module is untouched (not matched)
  while the root-level scratch script is correctly ignored; also confirmed `git status --short tests/` shows
  no changes to the real suite.

**Not done — deliberately out of scope for this task:** the older, pre-existing, possibly-public token
exposure at commit `2c38035` on `master`/`origin` (see Unplanned section above) is unrelated to any of these
files and remains an open question for the user, not something Task 0.11's doc/hygiene scope covers.

**Verified:** full suite green (39 tests, unchanged — this task touches no test-covered logic paths, only
config/docs/CORS/bind-address/gitignore).

---

**Phase 0 complete: 11/11 tasks done, 39 tests passing.**

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

## Phase 1 — Record Everything

> Pure addition, no behaviour change. Appends tick/feature-vector capture so the dataset starts
> accumulating immediately; nothing in Phase 0's decision logic is touched.

### Task 1.1 — TickRecorder

**Files:** `trading_copilot/journal/tick_recorder.py`, `trading_copilot/journal/__init__.py` (new). Commit
`c821e19`.

Append-only tick capture: buffers `(token, ts_ms, ltp, vtt, oi, bid1, ask1, bid_qty, ask_qty)` tuples and
flushes to date-partitioned, zstd-compressed parquet under `paths.TICKS_DIR`, either explicitly or once
`flush_n` rows accumulate. Pure addition, not yet wired into the live ingest path (Task 1.2). 3/3 tests pass.

### Task 1.2 — Wire TickRecorder into ingest

**Files:** `trading_copilot/rolling_state_engine.py`, `trading_copilot/data_services/upstox_feed.py`,
`tests/test_recorder_wiring.py` (new). Commit `ab74978`.

`RollingStateEngine.__init__` now attaches a `TickRecorder(TICKS_DIR)`; a class-level `recorder = None`
default keeps every existing `__new__`-bypass test fixture in the suite working without touching disk (none
of them set a recorder). `process_tick` records every tick — including options ticks and ticks with no
order book — before any branching, so the recorded stream is faithful to what the feed actually sent, not
just what happens to reach the phantom-candle path. `upstox_feed.start_upstox_service` gained a
`tick_flush_worker` flushing the buffer to disk every 30s off the event loop thread.

**Deviation from the plan:** the plan's own end-to-end verification (kill the feed for two minutes during
market hours, inspect the parquet output) needs a live market session — not reproducible in this
environment. Substituted `tests/test_recorder_wiring.py`, which locks in that every tick reaching
`process_tick` is forwarded to the attached recorder with the correct fields, that a missing order book
records zeros rather than crashing, and that an engine with no recorder attached still works. 3/3 tests pass.

### Task 1.3 — FeatureLog

**Files:** `trading_copilot/journal/feature_log.py` (new). Commit `ec4dd3f`.

`FeatureRecord` flattens an arbitrary `features` dict into `f_<name>` columns and a `staleness` dict into
`stale_<name>` columns; `FeatureLog` buffers and flushes the same way `TickRecorder` does. Heterogeneous
feature sets across writes are handled by pandas' union-of-columns behaviour (missing → NaN) — tested
explicitly, since the feature set the composite scorer emits will grow over time. 3/3 tests pass.

### Task 1.4 — Wire FeatureLog into the gatekeeper loop

**Files:** `trading_copilot/reasoning_engine.py`, `tests/test_gatekeeper_feature_logging.py` (new). Commit
`34b85ac`.

A `FeatureRecord` is now written for every symbol on every 10s gatekeeper tick, **before** the 3-tick
debounce `continue` — a debounced-out tick is still recorded, not only the ticks that end up changing the UI
card. This is what makes the 102 hand-picked thresholds tunable later: rejections and gated setups (the
counterfactual — what the system did *not* take) are logged, not just fired signals.

Decision classification (`PROPOSED` / `REJECTED_<reason>` / `GATED_<reason>`) and numeric-field flattening
were pulled out as pure module-level functions (`_classify_decision`, `_numeric_features`) specifically so
they're directly unit-testable — the loop itself is an infinite `while True` mutating class-level dicts and
isn't reasonably driven in a test.

**Deviation from the plan:** the plan's own verification ("run one session, inspect the decision distribution
and column count in `data/features/*.parquet`") needs a live session, same as Task 1.2 — deferred to manual
verification. 4/4 tests pass.

### Task 1.5 — Bar-accurate outcome labelling and pending-signal recovery (fixes C-3)

**Files:** `trading_copilot/journal/outcome_labeller.py` (new), `trading_copilot/signal_ledger.py`,
`trading_copilot/data_services/upstox_feed.py`. Commit `0460308`.

- **C-3b/c (bar-accurate labelling, cost floor):** the resolver previously sampled `current_ltp` once every
  60s against stop/target, so a touch-and-recover inside a minute was invisible, and a +0.001% move at the
  60-minute mark counted as a win. `label_outcome()` walks the actual 5-minute bars' high/low within the
  horizon window, and requires clearing a round-trip cost floor
  (`SignalLedger.ROUND_TRIP_COST_PCT = 0.06`, documented as a placeholder superseded by Task 3.1's real cost
  model) before a move counts as directionally correct.
- **Genuine architectural gap found while implementing step 6:** the plan says to source bars "from the
  owning RollingStateEngine's `ltf_df`" as if this were a same-process call. Traced the actual process
  topology: `RollingStateEngine` (and its `ltf_df`) lives only inside the `upstox_feed.py` process
  (port 8001); `SignalLedger`'s resolver runs inside `main.py`'s process (port 8000) — confirmed by finding
  `api_server.py`'s existing `aiohttp.ClientSession().get("http://127.0.0.1:8001/state")` poll, which exists
  for exactly this reason (documented in the codebase as the D-5 "2 Hz semantic pipeline" cross-process
  transport). Built the same bridge for bars: `GET /api/bars` on the `upstox_feed.py` app
  (`build_bars_response` is the pure, unit-tested half) and `SignalLedger._fetch_recent_bars()` to pull them,
  following the established pattern rather than inventing a new one.
- **C-3d (restart recovery):** `SignalLedger.recover_pending()` rebuilds `_pending_signals` from disk on
  startup, called at the top of `start_outcome_resolver`. Previously every signal younger than its resolution
  horizon at shutdown stayed `PENDING` forever and was silently excluded from every statistic after a
  restart.
- `SignalLedger._resolve_one()` is the pure per-signal resolution step (no network, no event loop) so the
  30m/60m/early-resolution logic is directly testable; the resolver's `while True` loop is now a thin driver
  that fetches bars and calls it.

**Verified:** 11 new tests across 4 new test files, all pass; full suite green (63 tests total).

**Phase 1 complete: 5/5 tasks done, 63 tests passing.**

---

## Phase 2 — Purity, Config, Single-Writer

> Exit criteria: all 102 thresholds live in YAML; `evaluate()` is a pure function under test; the display
> path cannot mutate decision state; ingest is single-writer; the perf hotspots are gone.

**Status: ✅ complete — 7/7 tasks done. 86 tests passing.**

### Task 2.1 — PolicyConfig

**Files:** `trading_copilot/config/policy_v1.yaml`, `trading_copilot/core/__init__.py`,
`trading_copilot/core/policy_config.py` (new). Commit `fd75341`.

`config/policy_v1.yaml` collects every threshold previously hardcoded across `semantic_tagger.py`,
`conviction_scorer.py`, `intraday_gatekeeper.py`, and `regime_manager.py` into one auditable, versioned
file. **Every value was verified against the actual current source (line-by-line `grep`/`Read`) before
transcription** — not taken from the plan's YAML block on trust, because Tasks 0.9 and 0.10 both turned up
literal-value mismatches between this plan's pseudocode and the real code. All values matched.

`core/policy_config.py`: `PolicyConfig` is a frozen (immutable) dataclass; `load_policy()` reads + validates
the YAML and raises `ValueError` on any missing required section (`semantic`, `regime`, `conviction`,
`gates`, `horizon`).

**Documented exception (not a silent behaviour change):** `semantic.whale_slope_gate_adv_frac: 0.002` does
**not** correspond to any current code path. It is the intended ADV-normalised replacement for the
unnormalised `abs(whale_cvd_slope) > 50` magic number at `semantic_tagger.py:67`, but wiring that call site
is out of scope for a task whose stated exit bar is "reproduce today's behaviour exactly." Flagged in the
YAML header comment and the commit message.

**Scope note:** this task builds the loader only. Rewiring individual call sites
(`semantic_tagger.py`/`conviction_scorer.py`/`intraday_gatekeeper.py`) to *read* from `PolicyConfig` instead
of their inline constants is not a named step anywhere in this plan and is left as follow-up work — the YAML
+ loader exist so the values are auditable and available for the Phase 4 fitting work.

**Verified:** 4/4 new tests pass; full suite green (67 tests).

### Task 2.2 — Split read from write (`advance_state`) (fixes A-4)

**Files:** `trading_copilot/regime_manager.py`, `trading_copilot/conviction_scorer.py`,
`trading_copilot/reasoning_engine.py`, `tests/test_state_isolation.py` (new). Commit `934e16d`.

The regime FSM and whipsaw shield were defeated by a second, faster caller: `api_server.py`'s 2 Hz display
refresh called `determine_regime()` and `score_setup()` purely to render a value, mutating the same
hysteresis buffer, epoch counter, and polarity-flip counter the 0.1 Hz gatekeeper loop depends on. That
collapsed the 30 s hysteresis window to ~1.5 s (13× faster) and could fire the whipsaw shield on noise the
decision loop never saw.

- `RegimeManager.peek_regime()` — reads `current_regime`/`session_phase`/`epochs_in_regime` without touching
  `memory.buffer`, `current_regime`, or `epochs_in_regime`.
- `ConvictionScorer.score_setup(..., advance_state: bool = False)` — the `polarity_flips_today` and
  `previous_bias` mutations (and the post-penalty bias re-evaluation) only run when `advance_state=True`.
- `ReasoningEngine.build_structured_payload(..., *, advance_state: bool = False)` — peeks by default; calls
  `determine_regime`/`score_setup` with `advance_state=True` only when the caller passes it.

**Exactly one call site sets `advance_state=True`:** the main gatekeeper loop in
`start_global_gatekeeper_loop` — the single authoritative 0.1 Hz decision cadence this state is meant to
track.

**Two other call sites audited, left at the new default (`False`):**
1. `api_server.py`'s 2 Hz display `build_structured_payload` call — the bug this task fixes. (No edit
   needed; the new kwarg defaults to `False`.)
2. `analyze_stock`'s own `build_structured_payload` call, used by the user-triggered
   `POST /api/reasoning/instant/{symbol}` endpoint. **Not named in the plan** (which only lists two call
   sites). Reasoned through: it's a human-driven, un-rate-limited action; letting it advance regime/whipsaw
   state would reintroduce the exact defect category this task removes (an uncontrolled second caller
   mutating shared hysteresis state) if a user clicks it repeatedly across symbols. Left as peek.

**Verified:** 3/3 new tests pass; full suite green (70 tests).

### Task 2.3 — Wire MTFFeatureExtractor (fixes A-5)

**Files:** `trading_copilot/rolling_state_engine.py`, `trading_copilot/mtf_extractor.py`,
`tests/test_mtf_wiring.py` (new). Commit `d9b6a22`.

`MTFFeatureExtractor.extract_all()` was fully implemented and correct but **never called**, so
`fractal_alignment` / `volatility_state` / `elasticity_risk` / `kinetic_divergence` were permanently pinned
to `SemanticTagger`'s defaults on every symbol every tick. `RegimeManager._evaluate_candidate()` branches on
exactly those fields, so `PRE_BREAKOUT_SQUEEZE` (needs `"SQUEEZE"` in `volatility_state`) and
`MEAN_REVERSION_IMMINENT` (needs `"OVERSTRETCHED"` in `elasticity_risk`) could never fire — two of five
documented regimes were dead code, biasing everything toward `RANGE_BOUND_CHOP`.

Wired into `RollingStateEngine._compute_symbol`, immediately before `TerminalDashboard.update_state`:
`final_payload.update(MTFFeatureExtractor.extract_all(final_payload, final_payload['ltp']))`. Also deleted
the dead `bandwidth` local in `calc_volatility_state` (computed, never read, carrying a comment admitting
uncertainty about its own formula).

Both fixture cases in the plan's test were traced by hand before writing the test — the extractor's logic
was already correct, only the call was missing; the regression guard asserts `"MTFFeatureExtractor"` appears
in `_compute_symbol`'s source.

**Verified:** 3/3 new tests pass; full suite green (73 tests).

### Task 2.4 — Queue-based single-writer ingest (improved §4.10)

**Files:** `trading_copilot/data_services/upstox_feed.py`, `trading_copilot/rolling_state_engine.py`,
`tests/test_ingest_queue.py` (new). Commit `437cd0a`.

Three OS threads (macro/equity/options WS callbacks) were calling `process_tick()` directly, mutating
`phantom_candles` / `ltf_df` while `calculate_technicals_loop` read them — "safe" only per a comment
claiming GIL protection, which does not cover the multi-step read-modify-write in `process_tick`.

- `parse_tick(instrument_key, feed_data, reverse_map) -> Tick | None`: the protobuf-dict flattening lifted
  out of `_on_market_update` into a pure module-level function (`Tick` = frozen dataclass). Now the only
  work the callback threads do.
- `_on_market_update`: parse-and-enqueue only, via `self.loop.call_soon_threadsafe(tick_q.put_nowait,
  tick)`. The write-only `live_market_data` accumulation (grepped — never read anywhere) is dropped.
- `self.loop = asyncio.get_running_loop()` captured at the top of `start_multiplexer` before any producer;
  WS threads marked `daemon=True`.
- `RollingStateEngine.ingest_loop()`: the single consumer, the only `process_tick` caller. Accepts a `Tick`
  or a plain dict. Tick→parquet capture stays in `process_tick` (Task 1.2), so the recorder call is **not**
  duplicated in `ingest_loop`.
- `tick_q = asyncio.Queue(maxsize=100_000)` in `__init__`; `queue_depth` property; started via
  `asyncio.create_task` in `start_upstox_service` before the multiplexer.
- `GET /state` now also reports `queue_depth` (`-1` if the engine isn't attached yet).

**Verified:** 4/4 new tests pass; full suite green (77 tests).

### Task 2.5 — Stream supervision and staleness (fixes A-12)

**Files:** `trading_copilot/data_services/upstox_feed.py`, `trading_copilot/rolling_state_engine.py`,
`trading_copilot/intraday_gatekeeper.py`, `trading_copilot/templates/index.html`,
`tests/test_gatekeeper_staleness.py` (new). Commit `a0cd86a`.

- `UpstoxStreamManager._supervise(name, streamer, keys, mode)`: one task per stream.
  `streamer.connect()` blocks its daemon thread until the socket drops; on thread exit the supervisor
  reconnects with capped exponential backoff (1s→60s, reset to 1s once healthy), wrapped in try/except so a
  throwing reconnect can't kill the supervisor. `start_multiplexer` launches three `_supervise` tasks
  instead of three bare threads.
- `RollingStateEngine.last_tick_ts[token]` set in `process_tick`; `_compute_symbol` publishes
  `final_payload['data_age_s']`.
- `IntradayGatekeeper.evaluate`: STALE FEED SHIELD right after the market-open guard — `data_age_s > 15`
  returns `Wait` / `llm_authorized=False` / `math_rejection="STALE_DATA_<n>s"`.
- `index.html`: hidden `#stale-banner` (amber) shown by `updateStaleBanner()` when any LIVE symbol's
  `data_age_s > 15`. `node --check` clean on the edited inline script block.

**Not auto-testable** (needs a live socket to drop): the `_supervise` reconnect loop, and whether
`MarketDataStreamerV3.connect()` is re-invokable on the same instance after a drop — flagged here for
manual verification. The staleness propagation and gatekeeper shield are covered: 3/3 new tests pass; full
suite green (80 tests).

### Task 2.6 — Retire the performance hotspots (fixes D-1, D-2, D-3)

**Files:** `trading_copilot/performance_analyzer.py`, `trading_copilot/technical_engine.py`,
`trading_copilot/rolling_state_engine.py`, `trading_copilot/data_services/upstox_feed.py`,
`tests/test_perf_hotspots.py` (new). Commit `fecb66e`.

- **D-1:** `PerformanceAnalyzer.get_feedback_payload` re-read the whole signal ledger ~100×/sec
  (`conviction_scorer` calls it per scored symbol per cycle; each call did two `load_all_signals` scans).
  Now a 300s TTL cache + a single load per miss; `compute_regime_accuracy` /
  `compute_symbol_accuracy` / `compute_dashboard` share one load via new `_regime_accuracy_from` /
  `_symbol_accuracy_from`. `invalidate_cache()` added. Test: 50 calls → ≤2 loads.
- **D-2:** `_compute_symbol` re-opened `macro_baselines.json` and `institutional_flow.json` once per
  symbol per 1.5s cycle. Now mtime-keyed caches (`_get_baselines` / `_get_flow_state`) — re-read only when
  the file actually changes. The ~6 MB `save_cache()` JSON dump moved off the event loop via
  `asyncio.to_thread`.
- **D-3:** two per-row Python loops on ~1650-row frames every cycle:
  - `calc_institutional_volume`'s `df.apply(calc_tod_z, axis=1)` → vectorised `map`/`where`;
    `test_tod_zscore_vectorised_equals_row_apply` proves bit-equality to the old row logic.
  - `calc_volume_profile_high_fidelity`'s accumulation loop → `np.bincount(weights=...)`;
    `test_bincount_profile_equals_the_old_accumulation_loop` proves bit-equality.
- **D-4** (yield between symbols) was already done in Task 0.8.

**Deliberate deviation:** the plan's Step 6 slices the MathEngine input to `target_df.tail(400)`. 400
5-minute bars is ~5 sessions, which would quietly shrink the time-of-day volume z-score baseline
(`groupby('time')` over *all* history) and the 4h omni-resample depth — an untested behaviour change. Used
`tail(2000)` (~27 sessions) instead: it bounds unbounded multi-day frame growth without touching the
≤20-session semantics anything actually consumes; RSI/MACD/BB are numerically identical well before 2000
bars regardless.

**Verified:** 4/4 perf tests (2 are loop-equivalence proofs); full suite green (84 tests).

### Task 2.7 — Session-anchored resampling (fixes C-4)

**Files:** `trading_copilot/technical_engine.py`, `tests/test_resample_anchor.py` (new). Commit `ef317a9`.

`df.resample('4h')` / `.resample('1h')` default to a midnight origin. Against an NSE session of
09:15–15:30, the first "4h" bar of the day spanned 08:00–12:00 and actually held only 09:15–12:00 = 2h45m
of real session — a dimensionally wrong bar feeding the omni-timeframe trend/EMA math.
`generate_omni_dataframes` now passes `origin=<09:15 of the frame's first day>` to every resample; 24h is
divisible by all five frequencies so the origin tiles cleanly and each session re-anchors at 09:15. Used
`df.index.min()` rather than the plan's `df.index[0]` (order-independent).

**Verified:** 2/2 new tests pass (one asserts the 1h open; one asserts 4h bars re-anchor each session over a
3-day frame and are never midnight-aligned); full suite green (86 tests).

**Phase 2 complete: 7/7 tasks done, 86 tests passing.**

---

## Phase 3 — Risk, Horizon, Cost

> Exit criteria: every proposal carries a quantity; correlated clusters can't exceed their risk budget;
> expectancy is net of real costs; one horizon governs prompt, measurement, and exits.

**Status: ✅ complete — 5/5 tasks done. 103 tests passing.**

### Task 3.1 — Cost model (improved §4.3.2)

**Files:** `trading_copilot/config/costs.yaml`, `trading_copilot/core/costs.py` (new),
`trading_copilot/conviction_scorer.py`. Commit `163344d`.

`round_trip_cost_pct(entry, exit_px, cfg)` sums both legs' brokerage (capped Rs.20/order), STT (sell),
exchange txn + SEBI (both legs), stamp duty (buy), and GST on the charge components, as % of entry notional.
`net_reward(...)` subtracts that plus a spread term.

**Design note (why `test_cost_scales_with_notional` passes robustly, not on FP noise):** the function only
gets `(entry, exit_px)`, but the Rs.20 brokerage cap makes real cost-% order-size dependent. `costs.yaml`
carries a `ref_qty` (100) — the assumed order size for the cost-% calc only — so the cap can bite; the L3
layer recomputes against the actual sized qty.

**Deviation:** the plan's Step 5 pseudocode drops the existing `0.1*ATR5m` slippage and uses `cost_abs`
alone. Kept both in `effective_risk`/`effective_reward` — market-impact slippage and statutory charges are
separate costs and §4.3.2 itself says "plus spread". Slightly stricter than the plan's version, which is the
stated intent.

**Verified:** 3/3 new tests pass; full suite green (89 tests).

### Task 3.2 — Cluster map (improved §4.3.1)

**Files:** `trading_copilot/config/clusters.yaml`, `trading_copilot/core/risk.py`,
`trading_copilot/core/types.py` (new). Commit `3cab9fc`.

`clusters.yaml` partitions the 27-name watchlist (ADANI×4, POWER_CAPGOODS×5, PSU_METALS_INFRA, FINANCIALS×7,
TELECOM, IT, HEALTHCARE, SHIPPING, EV; RELIANCE deliberately its own). `load_clusters()` inverts to
`{symbol: cluster_id}`; `cluster_of(symbol, map)` returns the cluster or the symbol when unmapped. This
commit also lands `core/types.py` (Proposal/SizedProposal/Rejection frozen dataclasses) and the rest of
`core/risk.py` (RiskLimits, Portfolio, `size()`) as the shared foundation for 3.3 — inert until 3.3 wires
it.

**Deviation:** BHEL and SCI each appear in two of the plan's overlapping prose groups; a symbol can only be
in one cluster. BHEL → POWER_CAPGOODS, SCI → its own SHIPPING. The three tests (Adani grouping, power
grouping, unmapped fallback) all pass.

**Verified:** 3/3 new tests pass; full suite green (92 tests).

### Task 3.3 — Position sizing and exposure limits (improved §4.3)

**Files:** `trading_copilot/config/risk.yaml` (new), `trading_copilot/reasoning_engine.py`,
`trading_copilot/templates/index.html`. Commit `708f37e`.

`core.risk.size(proposal, portfolio, limits, adv_shares)` enforces, in order: DEGENERATE_STOP guard,
DAILY_LOSS_LIMIT breaker, per-trade risk (`risk_per_trade_pct` of capital at the stop), liquidity cap
(`max_adv_participation` of ADV), aggregate per-cluster risk budget (`CLUSTER_LIMIT_<id>` /
`SIZE_ROUNDS_TO_ZERO`).

`ReasoningEngine._attach_sizing` runs after `IntradayGatekeeper.evaluate` authorises a proposal — the card
gains `Qty` / `Risk_Amount` / `Risk_Rejection`. `_build_portfolio` reconstructs the open-risk book from
`user_positions` (`qty*|entry-stop|` per position, mapped to its cluster; positions missing a numeric qty or
stop contribute nothing). `index.html renderReasoningReport` gets a "Qty (L3)" cell + a sized-risk /
risk-layer-blocked banner (`node --check` clean).

**Known limitation (documented):** no realised-P&L feed in this process, so
`Portfolio.realized_loss_today` is always 0.0 — the daily-loss breaker is a no-op here until a P&L source is
wired (Phase 4/5). Per-trade, liquidity and cluster caps are fully active.

**Verified:** 5/5 new tests pass; full suite green (97 tests).

### Task 3.4 — Unify the horizon (improved §4.4)

**Files:** `trading_copilot/intraday_gatekeeper.py`, `trading_copilot/signal_ledger.py`,
`trading_copilot/performance_analyzer.py`, `trading_copilot/config/policy_v1.yaml`,
`trading_copilot/templates/index.html`. Commit `b593336`.

One `policy_v1.yaml` `horizon` block is now the source of truth (primary 90m, entry_cutoff 13:45,
square_off 15:20, measure_at [30, 90]).

- Gatekeeper Path B: HORIZON CUTOFF rejects any new entry after `entry_cutoff_ist` with
  `math_rejection="ENTRY_CUTOFF_<hhmm>"`, `llm_authorized=False`.
- `SignalLedger`: `_pending_signals` re-keyed from hardcoded `target_30m`/`target_60m` to
  `targets`/`resolved` dicts built from `_measure_at()`. `_resolve_one` loops the checkpoints, writes
  `pnl_<n>m_pct` / `directional_correct_<n>m` per checkpoint, marks RESOLVED when the primary (90m) lands;
  an intrabar stop/target hit at any checkpoint still resolves early.
- `PerformanceAnalyzer._compute_metrics`: optimises `win_rate_90m` / `win_rate_primary`, keeps
  `win_rate_30m` as a diagnostic, drops `win_rate_60m`; best/worst-regime selection keys off
  `win_rate_primary`.
- Stagnation gate 30m → 60m (gatekeeper + policy).
- Prompt: "2-6 hour" → "~90-minute primary + 30m diagnostic; no entry after 13:45 IST, square-off 15:20".

`test_bar_accurate_resolution.py` / `test_pending_recovery.py` updated for the checkpoint-keyed structure.

**Verified:** 2/2 new tests pass; full suite green (99 tests).

### Task 3.5 — Emit tags AND values; renormalise the composite (improved §4.7, C-1)

**Files:** `trading_copilot/semantic_tagger.py`, `trading_copilot/conviction_scorer.py`,
`trading_copilot/regime_manager.py`, `trading_copilot/intraday_gatekeeper.py`. Commit `6463090`.

- §4.7: Block-1/2/3 fields with a real underlying scalar are now `{"state": <tag>, <scalar>: <value>}`
  (`volume_regime.vol_z`, `order_book_imbalance_state.obi`, `session_cost_basis_state.vwap_atr_ratio`,
  `flow_divergence_state.{whale_slope, price_to_vwap_pct}`, `volatility_regime_state.iv_pct`,
  `options_gravity_state.mp_atr_ratio`, `pcr_regime.pcr_pct`). Pass-through fields stay bare strings. New
  module-level `state_of(v)` normalises both shapes, wired into every string-comparison consumer
  (RegimeManager unwraps at `_extract_regime_slice`'s boundary so `_evaluate_candidate` is untouched;
  ConvictionScorer; IntradayGatekeeper). The UI reads none of these fields — no JS change.
- C-1: `ConvictionScorer._normalized_weights(regime, catalyst_live)` renormalises the component weights over
  the live components only. Catalyst is a hardcoded 0.0 pass-through, so `w_cat` (0.10–0.25) was bleeding
  weight into nothing and capping `|composite|` below 1.0 (0.75 in TREND_EXPANSION). With catalyst dead,
  `w_cat` drops from the denominator; the three live components sum to 1.0. `score_setup` uses it.

**Verified:** 4/4 new tests pass; full suite green (103 tests) — every SemanticTagger consumer still passes.

**Phase 3 complete: 5/5 tasks done, 103 tests passing.**

---

## Phase 4 — Measurement

> Exit criteria: the system reports only confidence it has measured, and the LLM's contribution is
> quantifiable.

**Status: ✅ complete — 3/3 startable tasks done (4.1, 4.2, 4.4). 4.3 is data-gated and correctly not
started. 125 tests passing.**

### Task 4.1 — Shadow-mode both-arm logging (improved §4.5)

**Files:** `trading_copilot/journal/arms.py` (new), `trading_copilot/reasoning_engine.py`,
`trading_copilot/api_server.py`. Commit `fa90e2a`.

The architecture's whole premise is that the LLM adds judgment over the math layer — but only
`CONFIRM`/`ADJUST` verdicts reached the ledger, so every `ABORT`/`DEFER` vanished and the premise could not
be tested even in principle.

- `ArmRecord` (symbol, ts, config_version, math_arm, llm_arm, escalated) + `ArmJournal`, append-only JSONL
  under `SIGNALS_DIR/arms`, one file per IST session date. `load_all()` dedupes on `arm_id` so a re-written
  (labelled) row supersedes its original.
- `ReasoningEngine._write_arm_record` fires on **every** escalation, before and independent of the
  actionable-directive gate.
- `label_arm_record()` scores **both** arms against the same bars with Task 1.5's `label_outcome`. The math
  arm is always scored (that is the counterfactual); the LLM arm only when the verdict actually took a
  trade, so a veto's outcome stays `None` and the math arm alone says whether the veto was right.
- `resolve_arms()` labels records whose horizon has elapsed; wired into the gatekeeper loop as a 5-minute
  task, reusing the ledger's cross-process bar bridge through an **injected fetcher** (no import coupling).
- `summarise_arms()` + `GET /api/performance/arms`: win rate, avg R and count per arm, plus **LLM veto
  precision** — the share of vetoed proposals whose math arm would in fact have lost.

**Also fixed here:** a wall-clock-fragile test I had introduced in Task 3.5.
`test_semantic_block_carries_the_scalar_alongside_the_tag` never pinned `is_market_open`, and
`translate_to_llm_payload` zeroes all live microstructure when the market is closed — so it passed on a
weekday afternoon and failed every weekend (discovered because 2026-09-12 is a Saturday). Same defect class
as Task 0.9. Swept the whole suite for the pattern afterwards; no other test was unpinned.

**Verified:** 7/7 new tests pass; full suite green (110 tests).

### Task 4.2 — Report `None` until calibrated (improved §4.2, fixes C-1)

**Files:** `trading_copilot/core/calibration.py` (new), `conviction_scorer.py`, `intraday_gatekeeper.py`,
`reasoning_engine.py`, `config/policy_v1.yaml`, `templates/index.html`. Commit `3ddb86c`.

`implied_probability` was `0.50 + 0.85*(sigmoid(4.5*|composite|) - 0.50)` — a monotone rescaling of the
score with two magic constants, never fitted against a single realised outcome, yet rendered to the operator
as confidence and handed to the LLM as "ground truth".

- `Calibration(b0, b1, n_resolved, fitted_at, buckets)` with `MIN_N = 200`; below that `.predict()` returns
  `None`. `load_calibration()` returns `None` when `data/calibration.json` is absent (it is).
  `implied_probability()` takes `regime` so a future per-regime fit is a one-site change.
  `calibration_status()` renders `unmeasured (n=0 < 200)`.
- Sigmoid deleted. `expectancy_matrix` now always carries `reward_risk` and `calibration_status`;
  `implied_probability` and `statistical_edge` are `None` while uncalibrated. `breakeven_probability`
  survives — it comes from the geometry, not a guess.
- Admission gate switches: measured `stat_edge` when calibrated, else
  `reward_risk >= conviction.min_reward_risk` (new, 1.5) with a distinct `INSUFFICIENT_REWARD_RISK` reason.
- **Downstream `None`-safety:** `IntradayGatekeeper` and `analyze_stock` both did `stat_edge > 0` and
  `stat_edge >= 0.05`, which would now raise `TypeError` on `None`. Both fall back to reward:risk and report
  **no** confidence number rather than deriving one from a null edge.
- Prompt no longer calls `expectancy_matrix` ground truth; it tells the LLM a null `implied_probability`
  means unmeasured and forbids inventing one.
- UI Action Plan card shows `Edge: unmeasured · R:R x.x` plus an explanatory line, instead of a fabricated
  Confidence score.

**Verified:** 9/9 new tests pass; full suite green (119 tests).

### Task 4.3 — Fit the calibration 🔒 **DATA-GATED — NOT STARTED (correct)**

Gate checked 2026-09-12: **`resolved signals: 84 (need 200) | feature days: 0 (need 20)`**. The plan states
*"Do not start this task before the trigger passes"* and deliberately omits detailed steps because the right
model form depends on an empirical distribution that does not exist yet. Fitting now would fit noise.

Worse than the raw count suggests: **all 84 resolved signals are legacy**, graded at the retired 30m/60m
horizons (pre-Task-3.4), so they carry no 90m outcome and do not count toward the 200 either. The usable
counter effectively restarts from zero. Re-run the plan's gate command after ~20 live sessions.

### Task 4.4 — Reliability view

**Files:** `core/calibration.py`, `api_server.py`, `templates/index.html`. Commit `0113b1b`.

While uncalibrated there is no predicted probability to plot, so the honest x-axis is `|composite|` itself —
which is exactly the diagnostic 4.3 needs first: a flat curve means the score has no discriminative power
and no calibration will rescue it.

- `reliability_buckets()`: realised win rate per `|composite|` decile against the diagonal a calibrated
  score would sit on. Buckets under `min_n` (10) are marked **suppressed rather than silently dropped**, so
  the operator sees where evidence runs out. Reports `win_rate_spread` and `has_discriminative_power`
  (≥ 10 pp between best and worst populated bucket).
- `GET /api/performance/reliability`; a Score Reliability panel on the dashboard with per-bucket n, win
  rate, diagonal reference and a bar with the diagonal marked, plus a verdict banner that says **FLAT
  CURVE** outright when the spread is small and **NOT ENOUGH DATA** when fewer than two buckets clear
  `min_n`.

**Found while smoke-testing against the real ledger:** 84 resolved signals exist on disk but every one was
graded at a retired horizon, so the view reported a bare `n=0`. Folding them in would mix horizons (a 60m
outcome is not a 90m one) and corrupt the curve; dropping them silently would show "no data" when data
plainly exists. They are now counted separately as `n_legacy_excluded` and named in the UI status line.

**Verified:** 7/7 new tests pass; full suite green (125 tests); `node --check` clean; endpoint smoke-tested
against the real on-disk ledger.

**Phase 4 complete: 3/3 startable tasks done, 125 tests passing. 4.3 remains gated by design.**

---

## Phase 5 — Operator Surfaces *(in progress — 2/6, paused after 5.2 by request)*

Executed via Subagent-Driven Development: a fresh implementer subagent per task, a task
review (spec compliance + code quality) against the actual diff after each, and a fix
loop when the review finds something. Ledger, briefs, reports and review packages live at
`.superpowers/sdd/2026-09-09-quantflow-remediation-and-measurability/` (git-ignored).

**Pre-flight rulings** (recorded before Task 5.1 started, after scanning all six tasks for
plan-vs-plan and plan-vs-code conflicts):

- The plan's own "Deliberate Gaps" section says *"disable the Discovery button until Task
  5.5 rebuilds the loop properly... Add to Task 5.5 as its first step"* — but the printed
  Task 5.5 step list (6 steps, all scoring-math fixes) never actually includes that step.
  Ruling: Task 5.5 gets an explicit Step 0 honoring the Deliberate Gaps instruction over
  the incomplete step list.
- `screener_engine.py` (Task 5.5's target) still constructs against `smart_connect`
  (Angel One SmartAPI), which doesn't exist anywhere in the live 4-process Upstox
  architecture. Ruling: Task 5.5's six listed steps are pure, unit-testable scoring/
  selection functions independent of the data source — fix those; do NOT rewire the
  screener onto Upstox, that's a separate unscoped migration.
- Task 5.6 asks to port `macro_eod_engine.fetch_market_breadth` (the correct NIFTY-50
  A/D calc) into `macro_worker.py` before deleting the old file. Found that the live `/ws`
  handler (`api_server.py:402-413`) already computes its own `ad_ratio` — a watchlist-only
  proxy — and always overrides whatever the stored `ad_ratio` field holds, so a naive port
  would ship correct code that nothing ever calls. Ruling: the ported fetch must actually
  feed the `/ws` payload (falling back to the watchlist proxy only when the fetch hasn't
  run yet), with the UI label reflecting which source is showing.

### Task 5.1 — Attention ranking (improved §4.6)

**Files:** `reasoning_engine.py`, `api_server.py`, `templates/index.html`, `tests/test_attention_rank.py`.
Commits `2b5e276`, `d543f7d` (fix round).

The plan's formula (`ev_r × confidence × freshness`) predates Task 4.2, which deleted the
invented-probability "confidence" field — there is no such field in the current payload.
Reconciled as `ev_r = statistical_edge if measured else (reward_risk - 1.0)`, `freshness =
1 / (1 + age_s/60)`. Rejected setups rank at `-inf` so they sink to the bottom without a
special case. `data_age_s` (already tracked per-tick) is now copied onto the structured
payload so `attention_rank(sp)` stays a single-argument pure function.

Fixed the debounce asymmetry named in the plan: the old `if count < 3: continue` blanked
the operator's card for the first two ticks of *any* state change, not just the LLM
escalation it was meant to protect. Now the card publishes every tick (marked `unstable`
while `count < 3`); only escalation to the LLM still requires 3 stable ticks. Escalation
is additionally capped to the top 5 ranked symbols per cycle, computed without a second
`build_structured_payload(..., advance_state=True)` call per symbol (Task 2.2 restricted
that mutation to exactly one call site per tick; a second call in the same tick would
double-mutate the whipsaw-shield state).

**Found in review, fixed in round 1:** the Live Action grid's client-side sort comparator
(`getAttentionRank(b) - getAttentionRank(a)`) returned `NaN` whenever both cards were
rejected/unranked (`-Infinity - (-Infinity) === NaN` in JS) — a common state, not an edge
case, since every suppressed setup renders that way. `Array.prototype.sort` is undefined
on a NaN-returning comparator, so the grid's ordering jittered across the 3s poll for any
set of suppressed cards. Fixed with explicit `!==`/`>` branching that handles `-Infinity`
correctly on both operand orders (commit `d543f7d`).

**Deviations:** top-N escalation re-evaluates only at the instant a symbol's debounced
advice *transitions* — a symbol that climbs into the top-5 without its own advice changing
won't retroactively escalate until it transitions again (scoped, documented, not a redesign
of the escalation trigger). The Live Action grid turned out to be fed by
`/api/reasoning/all_reports` (`latest_reports`), not `/ws`'s `global_state` as guessed when
dispatching the task — rank is attached at the correct endpoint.

**Not fixed (deferred, non-blocking):** `ATTENTION_TOP_N` is a separate constant in Python
and JS with only the Python side under test — nothing stops the two drifting apart.
`attention_rank` is computed twice per tick on two independently-built structured payloads
(the escalation-decision one, discarded; the `/ws` one, displayed) — very likely identical
in practice but not proven so by construction.

**Verified:** 140 passed / 0 failed after the fix round (was 125 before this task); `node
--check` clean on the modified inline scripts.

### Task 5.2 — Provenance panel (improved §5.1)

**Files:** `conviction_scorer.py`, `templates/index.html`, `tests/test_provenance_panel.py`.
Commit `3b265ad`.

`ConvictionScorer.score_setup` now returns a `contributions` dict (one entry per `micro`/
`struct`/`deriv`/`catalyst` category: `raw`, `normalized`, `weight`, `contribution`,
`firing_signals`) built from the function's own existing intermediate variables — not
recomputed, so it cannot drift from the real composite math. `catalyst` gets `raw: None`
(not `0.0`) plus `dead: True` and the marker `"⚠ no catalyst input (see C-1)"`, distinguishing
"never scored" from "scored as neutral" for a term that has been a hardcoded pass-through
since before this plan. `firing_signals` lists only the tags that actually matched a scoring
branch, not every non-empty tag.

`_create_rejected_output` now threads `contributions` through too, so a rejected setup still
shows the operator *why* — six of the seven rejection paths fire after category scoring and
get the real dict; only `MARKET_CLOSED` (the one guard that fires before scoring happens)
gets `None`.

The mockup's footer (`edge unmeasured (n=41 < 200)  staleness: micro 0.4s · deriv 271s`)
needed real per-block staleness numbers or nothing at all — never a fabricated one. `micro`
has one (`data_age_s`, already tracked). Investigated `struct` (`RollingStateEngine` tracks
a baseline-file mtime internally but never surfaces it to the payload), `deriv` (no age/
timestamp field anywhere in `derivatives_worker.py`'s output), and `catalyst` (`NewsEngine.
last_fetch_time` exists but only on a separate process's own `/state` endpoint, not on the
payload reaching the scorer) — none reachable without new cross-file or cross-process
plumbing, so all three render as `n/a` rather than an invented number. `edge` reuses the
pre-existing `calibration_status()` string verbatim.

**Not fixed (deferred, non-blocking):** only the `NEUTRAL_CONVICTION` rejection path has a
dedicated test among the six structurally-identical post-scoring paths that now carry
`contributions` (same one-line pass-through, verified by direct code reading, but untested
individually). `contribution` is computed before `normalized`/`weight` are independently
rounded for display, so the displayed triple can show a ~0.001 mismatch in edge cases
(tolerance-tested, does not affect the real composite math).

**Verified:** 147 passed / 0 failed; review approved with zero fix rounds.

**Phase 5 status: 2/6 done (5.1, 5.2). Paused after 5.2 by explicit request — 5.3 (Exposure
view), 5.4 (Replay runner), 5.5 (screener + discovery loop), 5.6 (session review + dead-code
removal) remain, briefs already prepared.**

---

## Open questions / follow-ups

- **PolicyConfig call sites not yet rewired (Task 2.1 scope note):** `config/policy_v1.yaml` + the loader
  exist, but `semantic_tagger.py` / `conviction_scorer.py` / `intraday_gatekeeper.py` still read their
  inline constants. Rewiring them to `load_policy()` is not a named step in this plan — follow-up work.
- **`semantic.whale_slope_gate_adv_frac` (0.002)** in `policy_v1.yaml` corresponds to no code path yet — the
  intended ADV-normalised replacement for `abs(whale_cvd_slope) > 50` at `semantic_tagger.py:67`.
- **`_supervise` reconnect not verified against a live drop (Task 2.5):** unknown whether
  `MarketDataStreamerV3.connect()` can be re-invoked on the same instance after disconnect; if not, the
  supervisor needs to recreate the streamer each loop.
- **L3 daily-loss breaker is a no-op (Task 3.3):** `Portfolio.realized_loss_today` is always 0.0 in the
  reasoning process — needs a realised-P&L feed (Phase 4/5) before the DAILY_LOSS_LIMIT rejection can fire
  in production. Per-trade / liquidity / cluster caps are live.
- **`costs.yaml` rate card unverified (Task 3.1):** rates are from the improved-arch doc, not a live broker
  card — verify before trusting the expectancy numbers. `ref_qty` (100) is a modelling assumption for the
  cost-% calc, not a real order size.
- **`ConvictionScorer._get_adaptive_weights` still reads `win_rate_30m`** for its feedback scaling (Task
  3.4 kept that key as a diagnostic); arguably should use `win_rate_primary` now — left as-is, not a named
  plan step.
- **Task 4.3 calibration is gated on data, not effort** — needs ≥200 signals resolved at the *90m* horizon
  plus ≥20 feature-days. The 84 legacy signals on disk do not count (retired horizon). Until then the
  system correctly reports `implied_probability: None` everywhere.
- **Arm-journal veto precision needs live escalations** — `summarise_arms` is unit-tested but the journal is
  empty until a session runs with the LLM enabled; the both-arm comparison table stays blank until then.
- **Legacy signal records (pre-Task-3.4 schema)** carry `pnl_30m_pct`/`pnl_60m_pct` and no 90m fields.
  `PerformanceAnalyzer` degrades them to 0 and the reliability view excludes them explicitly. A one-off
  backfill script could re-grade them at 90m from recorded bars if that history is ever wanted.

- **Security incident (Phase 0)** — awaiting user decision on token rotation and history rewriting (both the
  local `7e239ca` commit and the pre-existing `master`/`origin` exposure at `2c38035`).
- **`.env.example` TOTP seed (Task 0.11)** — replaced with a placeholder; if `XHRGR2YOVCEGBYT3MQEBLI6CV3POJD7L`
  was a live seed, rotate it in Upstox.
