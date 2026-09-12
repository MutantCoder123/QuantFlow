# QuantFlow Implementation — Task Tracker

**Plan:** [docs/superpowers/plans/2026-09-09-quantflow-remediation-and-measurability.md](docs/superpowers/plans/2026-09-09-quantflow-remediation-and-measurability.md)
**Specsheet:** [CHANGE_SPECSHEET.md](CHANGE_SPECSHEET.md)
**Branch:** `feat/measurability`

Legend: `[ ]` not started · `[~]` in progress · `[x]` done & committed · `[!]` blocked · `[-]` deliberately skipped

---

## Phase 0 — Foundation & P0 Correctness

> Exit criteria: `pytest` green; no fabricated values reach the UI; volume accounting correct; all paths resolve.

- [x] **0.1** Test infrastructure and branch — commit `46586d8`
- [x] **0.2** Centralise path resolution (fixes A-9) — commit `7e239ca`
- [x] **0.3** Correct VTT volume accounting (fixes A-2, A-3 guard) — commit `35acf4f`
- [x] **0.4** Single session-boundary reset (fixes A-3) — commit `e617706`
- [x] **0.5** Fix SignalLedger crash on held positions (fixes A-8) — commit `1954274`
- [x] **0.6** Remove the fabricated macro narrative (fixes A-1) — commit `5492c2c`
- [x] **0.7** Expose alert endpoints (fixes A-11, B-28) — commit `711dc25`
- [x] **0.8** Isolate per-symbol failures (fixes A-13, D-4) — commit `1bdcac8`
- [x] **0.9** Correct whale-CVD polarity check (fixes A-6) — commits `36ef5e9`, `24b5528`
- [x] **0.10** Clamp LLM prices server-side (fixes A-10) — commit `d2a87c2`
- [x] **0.11** Security and documentation hygiene (fixes E-1, E-2, E-3, B-21, B-29) — commit `7845524`

**Unplanned:** security fix — commit `27b1c93` untracked a live credential
(`trading_copilot/upstox_token.json`) accidentally re-added by 0.2's `git add -A`.
See [CHANGE_SPECSHEET.md](CHANGE_SPECSHEET.md#unplanned-security-incident) for
full detail and the still-open question for the user.

**Phase 0 status:** ✅ complete — 11/11 tasks done

---

## Phase 1 — Record Everything

- [x] **1.1** TickRecorder — commit `c821e19`
- [x] **1.2** Wire TickRecorder into ingest — commit `ab74978`
- [x] **1.3** FeatureLog — commit `ec4dd3f`
- [x] **1.4** Wire FeatureLog into the gatekeeper loop — commit `34b85ac`
- [x] **1.5** Bar-accurate outcome labelling + pending recovery (fixes C-3) — commit `0460308`

**Phase 1 status:** ✅ complete — 5/5 tasks done

## Phase 2 — Purity, Config, Single-Writer

- [x] **2.1** PolicyConfig (thresholds → YAML) — commit `fd75341`
- [x] **2.2** Split read from write via `advance_state` (fixes A-4) — commit `934e16d`
- [x] **2.3** Wire MTFFeatureExtractor (fixes A-5) — commit `d9b6a22`
- [x] **2.4** Queue-based single-writer ingest — commit `437cd0a`
- [x] **2.5** Stream supervision and staleness (fixes A-12) — commit `a0cd86a`
- [x] **2.6** Retire performance hotspots (fixes D-1, D-2, D-3) — commit `fecb66e`
- [x] **2.7** Session-anchored resampling (fixes C-4) — commit `ef317a9`

**Phase 2 status:** ✅ complete — 7/7 tasks done

## Phase 3 — Risk, Horizon, Cost

- [x] **3.1** Cost model — commit `163344d`
- [x] **3.2** Cluster map — commit `3cab9fc`
- [x] **3.3** Position sizing and exposure limits — commit `708f37e`
- [x] **3.4** Unify the horizon — commit `b593336`
- [x] **3.5** Tags + values; renormalise composite (fixes C-1) — commit `6463090`

**Phase 3 status:** ✅ complete — 5/5 tasks done

## Phase 4 — Measurement

- [x] **4.1** Shadow-mode both-arm logging — commit `fa90e2a`
- [x] **4.2** Report `None` until calibrated — commit `3ddb86c`
- [!] **4.3** Fit the calibration 🔒 **DATA-GATED — trigger not met, correctly not started**
- [x] **4.4** Reliability view — commit `0113b1b`

**Phase 4 status:** ✅ complete — 3/3 startable tasks done; 4.3 blocked on data by design

> **4.3 gate check (2026-09-12):** `resolved signals: 84 (need 200) | feature days: 0 (need 20)`.
> The plan says explicitly *"Do not start this task before the trigger passes"* — fitting now would fit
> noise. Re-run the gate command in the plan after ~20 live sessions.
> Note: all 84 resolved signals are **legacy**, graded at the retired 30m/60m horizons, so they carry no
> 90m outcome and do not count toward the 200 either. The counter effectively restarts from 0.

## Phase 5 — Operator Surfaces  *(in progress — 5/6)*

- [x] **5.1** Attention ranking (improved §4.6) — commits `2b5e276`, `d543f7d` (fix round: NaN sort comparator)
- [x] **5.2** Provenance panel (improved §5.1) — commit `3b265ad`
- [x] **5.3** Exposure view — commits `0adfb3f`, `edeca59` (fix round: test coupled to live config)
- [x] **5.4** Replay runner — commits `05aa358`, `5390537` (fix round: report corrections + docstring)
- [x] **5.5** Fix screener, close discovery loop (fixes A-15) — commits `6916eec`, `2fd3f85`, `23070d0` (2 fix rounds: churn_cap/core-duplication bugs, undefined showToast)
- [ ] **5.6** Session review + dead-code removal

> Executed via Subagent-Driven Development (fresh implementer per task + task review +
> fix loop). Ledger: `.superpowers/sdd/2026-09-09-quantflow-remediation-and-measurability/progress.md`.
> Pre-flight rulings recorded there: (a) Task 5.5 gets an explicit Step 0 — disable the
> Discovery/playbook-generate button + endpoint, per the plan's own Deliberate Gaps note
> that its step list omitted; (b) Task 5.5's screener fixes stay scoped to pure scoring/
> selection logic — rewiring `screener_engine.py` off the dead Angel One `smart_connect`
> path onto Upstox is a separate, unscoped migration; (c) Task 5.6's ported
> `fetch_market_breadth` must actually feed the live `/ws` payload (with the existing
> watchlist-only proxy as fallback), not sit unused next to it.

---

## Deliberate gaps (from plan self-review)

- [-] **A-7** playbook blocks event loop — folded into Task 5.5 (disable Discovery button rather than patch a feature being rewritten)
- [-] **A-14** parquet duplicate rows — verified `dupes: 0`; defect is in the code path, not the data
- [-] **C-2** wrong 5-year Value Area — nothing acts on those levels today; fix before they are wired in
- [-] **D-5** cross-process transport — re-measure after Phase 2 removes the 2 Hz semantic pipeline
