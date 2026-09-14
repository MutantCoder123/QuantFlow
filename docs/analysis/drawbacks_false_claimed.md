# QuantFlow / AlgoTrade — Drawbacks, False Claims & Better Approaches

> **Companion to** [Architecture_detailed_overview.md](Architecture_detailed_overview.md).
> Every finding below was confirmed by reading the cited code, and in several cases by executing
> path-resolution and data-file inspection against this repo. Findings I could **not** confirm without a
> live market session are explicitly marked **[unverified — needs runtime]** and are stated as risks,
> not facts.
>
> Where I initially suspected a defect and verification proved it *wasn't* one, I say so — those notes are
> in §H so the record is honest in both directions.

**Severity legend**

| | Meaning |
|---|---|
| 🔴 **P0** | Produces or displays wrong information a trader could act on. Fix before next live session. |
| 🟠 **P1** | Silently disables designed functionality, or can hang/crash a process. |
| 🟡 **P2** | Correctness is intact but the method is unsound, wasteful, or misleading. |
| ⚪ **P3** | Hygiene, dead code, docs. |

---

## Table of contents

- [§A Critical correctness defects](#a--critical-correctness-defects)
- [§B False and unimplemented claims](#b--false-and-unimplemented-claims)
- [§C Quantitative methodology weaknesses](#c--quantitative-methodology-weaknesses)
- [§D Performance and architecture](#d--performance-and-architecture)
- [§E Security and operations](#e--security-and-operations)
- [§F Dead code](#f--dead-code)
- [§G Better approach — prioritised remediation](#g--better-approach--prioritised-remediation)
- [§H Things I checked that turned out fine](#h--things-i-checked-that-turned-out-fine)

---

## §A — Critical correctness defects

### 🔴 A-1. The UI fabricates a bullish macro narrative and presents it as live analysis

[templates/index.html:641-648](trading_copilot/templates/index.html#L641-L648)

```js
// MOCK DATA FALLBACK FOR GLOBAL MARKET CONTEXT
if (!payload.global_market_context) {
    payload.global_market_context = {
        summary: "Global markets are showing strong resilience with cooling inflation data out of
                  the US and steady domestic inflows in Indian markets. FII selling pressure seems
                  to have absorbed. Key focus remains on upcoming Fed commentary and RBI policy
                  alignment.",
        sentiment: "BULLISH",
        timestamp: Math.floor(Date.now() / 1000)     // <-- stamped with the CURRENT time
    };
}
```

**Why this is the worst defect in the system.** This is not a placeholder in a dev view — it renders into
the production "Global Market Context" panel with a green `BULLISH` badge and a
`"Updated: <current time>"` label ([:657-661](trading_copilot/templates/index.html#L657-L661)). Nothing
distinguishes it from a real Gemini-generated macro read.

**When it fires:** whenever `global_market_context` is null — i.e. `news_feed.py` not running, `GEMINI_API_KEY`
missing, the Upstox news call failing, or simply the first ~30 minutes after startup before
`_macro_news_polling_loop` completes its first cycle
([news_engine.py:360-368](trading_copilot/news_engine.py#L360-L368)). That last case means **every session
begins by showing the operator a fabricated bullish macro thesis.**

**Impact:** it injects a directional prior into the human decision-maker, and it is a *fixed* bullish prior,
so the bias is systematic rather than random.

**Fix:** delete the block. Render an explicit `MACRO CONTEXT UNAVAILABLE` state.

```js
if (!payload.global_market_context) {
    badge.innerText = 'UNAVAILABLE';
    badge.className = '... bg-slate-800 text-slate-500 ...';
    summary.innerText = 'Macro context unavailable — news_feed (:8003) not reporting.';
    timeElem.innerText = '';
    return;
}
```

---

### 🔴 A-2. Phantom-candle volume accumulates a cumulative counter

[rolling_state_engine.py:217](trading_copilot/rolling_state_engine.py#L217)

The value arriving from Upstox is `vtt` — *volume traded today*, a **cumulative** running total
([upstox_feed.py:266](trading_copilot/data_services/upstox_feed.py#L266)):

```python
vol = full_feed.get("vtt", market_ff.get("vtt", 0))
...
self.rolling_engine.process_tick(..., volume=float(vol), ...)
```

`process_tick` then treats it as a per-tick delta:

```python
phantom = {..., 'volume': volume, ...}   # :206  seeded with the cumulative total
...
phantom['volume'] += volume              # :217  and adds the cumulative total again, every tick
```

Meanwhile `MicrostructureEngine` does it **correctly**, differencing the same field
([microstructure_engine.py:142-143](trading_copilot/microstructure_engine.py#L142-L143)):

```python
prev_vol = cls.last_vtt_state.get(token, vol)
tick_vol = max(0, vol - prev_vol)
```

So the two subsystems disagree about what "volume" means, and the phantom candle's figure grows without
bound over the 5-minute window.

**Blast radius** — everything downstream of `phantom['volume']`:

| Consumer | Path | Effect |
|---|---|---|
| `vol_z_score_5m` | [technical_engine.py:87-104](trading_copilot/technical_engine.py#L87-L104) | current bar compared against a real historical ToD baseline → z-score meaningless |
| `volume_regime` semantic | [semantic_tagger.py:82-89](trading_copilot/semantic_tagger.py#L82-L89) | `TIME_ADJUSTED_SHOCK` fires on an artefact |
| `micro_score *= 1.5` | [conviction_scorer.py:92-93](trading_copilot/conviction_scorer.py#L92-L93) | conviction inflated by 50 % on an artefact |
| `TREND_EXPANSION` regime | [regime_manager.py:88](trading_copilot/regime_manager.py#L88) | the *only reachable* trend branch (see A-5) keys off this |
| VWAP, CMF, 20 d volume profile | [technical_engine.py:71,108,305](trading_copilot/technical_engine.py#L71) | volume-weighted levels distorted |
| **`ltf_df` itself** | [rolling_state_engine.py:195](trading_copilot/rolling_state_engine.py#L195) | the corrupt phantom is **committed to the DataFrame** every 5 min, permanently poisoning the ToD volume baseline and the cached `cache_state.json` |

The last row is the serious one: this is not a transient display error, it **writes bad data into the
historical series** that all later z-scores are measured against.

**Fix** — difference in one place and pass a true delta:

```python
# rolling_state_engine.py — track per-token cumulative volume
prev_vtt = self._last_vtt.get(token)
tick_vol = 0.0 if prev_vtt is None else max(0.0, volume - prev_vtt)
self._last_vtt[token] = volume
...
phantom['volume'] += tick_vol          # and seed a new phantom with tick_vol, not `volume`
```

Better still: make `MicrostructureEngine` the single owner of VTT differencing and have `process_tick`
consume `micro_state['tick_volume']`, so the two can never diverge again.

---

### 🟠 A-3. Session state is reset inconsistently, and the VTT anomaly guard zeroes real volume

> **Correction (verified by execution).** An earlier draft of this section claimed the day-boundary VTT
> mismatch flatlines volume "for the entire next session." **That is wrong** — I ran it. Because
> `cls.last_vtt_state[token] = vol` is assigned unconditionally at
> [:151](trading_copilot/microstructure_engine.py#L151), the baseline self-corrects after **one tick**.
> The real cost is a single tick's volume per symbol per day: negligible. The two findings below survived
> verification; that one did not.

[microstructure_engine.py:69-75](trading_copilot/microstructure_engine.py#L69-L75) resets some state on
IST date change:

```python
if state['last_reset_date'] != current_date_str:
    state['cumulative_pv'] = 0.0
    state['cumulative_v']  = 0.0
    state['last_reset_date'] = current_date_str
    cls.cvd_state.pop(token, None)
    cls.whale_cvd_state.pop(token, None)
```

Four other per-token dicts are **not** in that list: `vol_profile_state`
([:8](trading_copilot/microstructure_engine.py#L8)), `whale_cvd_history`
([:13](trading_copilot/microstructure_engine.py#L13)), `last_vtt_state`
([:151](trading_copilot/microstructure_engine.py#L151)), and `last_bba_state`
([:159](trading_copilot/microstructure_engine.py#L159)).

**Finding 1 — the anomaly guard silently zeroes legitimate volume.**
[:146-149](trading_copilot/microstructure_engine.py#L146-L149):

```python
if prev_vol > 100000 and tick_vol > (prev_vol * 0.5):
    logging.warning(f"VTT anomaly for {token}: tick_vol={tick_vol}. Clamping.")
    tick_vol = 0
```

Any increment exceeding **half of cumulative-volume-so-far** is discarded. That condition is routinely true
in the first ~30 minutes of a session, when cumulative volume is still small — exactly the `OPENING_RANGE`
window. Reproduced directly: feeding a normal cumulative sequence of 1,000,000 → 3,000,000 → 5,000,000
fires `VTT anomaly ... Clamping` **twice**, discarding 4,000,000 shares of genuine volume.

Downstream, that suppressed volume is what `vol_z_score` and `TIME_ADJUSTED_SHOCK` are supposed to detect —
so the guard preferentially destroys the signal it exists to protect.

**Fix:** the guard should catch *negative or resetting* counters (a genuine reconnect artefact), not large
positive increments:

```python
if vol < prev_vol:                      # counter went backwards -> reconnect/rollover
    tick_vol = 0.0
else:
    tick_vol = vol - prev_vol
```

**Finding 2 — POC is not intraday.** `vol_profile_state` accumulates forever, so `poc_price` and
`poc_distance_pct` are an *all-time-since-process-start* POC, not the session POC the naming implies.
`whale_cvd_history` likewise carries the previous session's samples into the new day's EMA and slope.

**Fix:** one session-boundary helper that resets *every* per-token dict, called from a single place — see
[improved_architecture_and_features.md §4.10](improved_architecture_and_features.md#410-make-ingestion-genuinely-single-writer),
where the queue consumer owns the boundary check.

---

### 🔴 A-4. The regime FSM and whipsaw shield are defeated by a second, 20×-faster caller

[`build_structured_payload`](trading_copilot/reasoning_engine.py#L57-L119) is **stateful** — it mutates the
per-symbol `RegimeManager` and `ConvictionScorer` singletons:

```python
manager      = RegimeManagerRegistry.get_or_create(symbol)      # :80
regime_meta  = manager.determine_regime(tactical_payload)       # :81  MUTATES buffer, epochs, current_regime
scorer       = ConvictionScorerRegistry.get_or_create(symbol)   # :86
math_setup   = scorer.score_setup(tactical_payload, payload)    # :87  MUTATES previous_bias, polarity_flips_today
```

It is called from **two** places at very different rates:

| Caller | Rate | Intent |
|---|---|---|
| [api_server.py:370](trading_copilot/api_server.py#L370) — WebSocket loop | **2 Hz × every symbol** | *display only* — embed `structured_payload` in the UI frame |
| [reasoning_engine.py:451](trading_copilot/reasoning_engine.py#L451) — gatekeeper | 0.1 Hz × every symbol | drive the actual decision |

The display path was clearly not intended to advance the state machines, but it does — and it dominates
by 20:1.

**Effect 1 — hysteresis is meaningless.** `RegimeManager` requires 2-of-3 agreement over
`SessionMemory.buffer` ([regime_manager.py:123-128](trading_copilot/regime_manager.py#L123-L128)).
Those 3 snapshots are now **1.5 seconds apart**, not 30 seconds. The `deque(maxlen=12)` covers
6 seconds of history instead of 2 minutes. The "hysteresis-protected FSM" filters sub-second noise only.

**Effect 2 — `regime_age_epochs` is nonsense.** It counts UI ticks, so a regime 2 minutes old reports
~240 epochs. This value is sent to the LLM as decision context
([regime_manager.py:142](trading_copilot/regime_manager.py#L142)).

**Effect 3 — the whipsaw shield mis-fires.** `polarity_flips_today` increments at 2 Hz
([conviction_scorer.py:156-158](trading_copilot/conviction_scorer.py#L156-L158)), so a genuinely oscillating
symbol reaches the 3-flip threshold within seconds and the 50 % composite penalty is applied and reset
repeatedly — turning a designed circuit-breaker into a source of score instability.

**Effect 4 — nondeterminism.** The gatekeeper reads whatever state the UI loop happened to leave behind.
Disconnect the browser and the decision path behaves differently, because `poll_upstox` still runs but
the WebSocket handler does not.

**Fix — split read from write.** This is the single highest-leverage structural change in the codebase:

```python
# reasoning_engine.py
@classmethod
def build_structured_payload(cls, symbol, payload, user_position=None,
                             user_intent=None, *, advance_state: bool = False):
    tactical = SemanticTagger.translate_to_llm_payload(payload)

    manager = RegimeManagerRegistry.get_or_create(symbol)
    tactical["market_regime"] = (manager.determine_regime(tactical) if advance_state
                                 else manager.peek_regime())      # pure read

    scorer = ConvictionScorerRegistry.get_or_create(symbol)
    tactical["math_setup"] = scorer.score_setup(tactical, payload,
                                                advance_state=advance_state)
    ...
```

`api_server.py:370` calls it with the default (`advance_state=False`);
`reasoning_engine.py:451` passes `advance_state=True`. `RegimeManager` gains a `peek_regime()` that returns
`{current_regime, session_phase, regime_age_epochs, just_transitioned:False}` without touching the buffer;
`ConvictionScorer.score_setup` guards its two mutations behind the flag.

---

### 🟠 A-5. Two of the five market regimes can never occur

The semantic fields `fractal_alignment`, `elasticity_risk`, `kinetic_divergence` and `volatility_state` are
read by `SemanticTagger` as **pass-throughs** from the flat payload
([semantic_tagger.py:108-111](trading_copilot/semantic_tagger.py#L108-L111)).

Their only producer is
[`MTFFeatureExtractor.extract_all`](trading_copilot/mtf_extractor.py#L100-L107) — **which nothing calls.**
Verified by reverse-import index: the sole import from `mtf_extractor` anywhere in the repo is
`sanitize_for_json` ([reasoning_engine.py:59](trading_copilot/reasoning_engine.py#L59)).

So all four fields permanently take their `.get()` defaults: `CONFLICTING_CHOP`, `EQUILIBRIUM`,
`MOMENTUM_CONFIRMED`, `NORMAL_RANGING`.

Trace that through [regime_manager.py:66-98](trading_copilot/regime_manager.py#L66-L98):

| Rule | Requires | Reachable? |
|---|---|---|
| `PRE_BREAKOUT_SQUEEZE` | `"SQUEEZE" in volatility_state` | ❌ always `NORMAL_RANGING` |
| `MEAN_REVERSION_IMMINENT` | `"OVERSTRETCHED" in elasticity_risk` | ❌ always `EQUILIBRIUM` |
| `TREND_EXPANSION` | momentum flow **and** (elevated volume **or** strong fractal) | ⚠️ volume branch only — fractal is always `CONFLICTING_CHOP` |
| `RANGE_BOUND_CHOP` | equilibrium flow **or** fractal chop | ✅ (fires trivially — fractal is *always* chop) |
| `TRANSITIONAL_DRIFT` | fallback | ✅ |

Knock-on effects:

- `ConvictionScorer` loses the fractal (±2) and elasticity (±2) terms — **4 of the ±10 micro range**
  ([conviction_scorer.py:80-89](trading_copilot/conviction_scorer.py#L80-L89)).
- The regime-specific hard kills for `MEAN_REVERSION_IMMINENT`
  ([:178-182](trading_copilot/conviction_scorer.py#L178-L182)) are unreachable.
- The `atr_mult` and 2nd-target logic keyed to `PRE_BREAKOUT_SQUEEZE`
  ([:230,245](trading_copilot/conviction_scorer.py#L230)) are unreachable.
- Because `RANGE_BOUND_CHOP` fires on the always-true `fractal == "CONFLICTING_CHOP"` branch, the system is
  **biased toward classifying everything as chop**, which then applies the ×0.6 gatekeeper dampening
  ([intraday_gatekeeper.py:164-165](trading_copilot/intraday_gatekeeper.py#L164-L165)).

**Fix** — wire the extractor into the flat payload, in `calculate_technicals_loop` right after the omni
metrics are available:

```python
# rolling_state_engine.py, after final_payload is assembled (~:365)
from mtf_extractor import MTFFeatureExtractor
final_payload.update(MTFFeatureExtractor.extract_all(final_payload, final_payload['ltp']))
```

Then re-check `calc_volatility_state` ([mtf_extractor.py:45-66](trading_copilot/mtf_extractor.py#L45-L66)),
which has an unused `bandwidth` variable and a comment admitting the author was unsure of the formula —
see §C-4.

---

### 🟠 A-6. Any long position is force-closed whenever whale CVD is merely negative

[intraday_gatekeeper.py:106-114](trading_copilot/intraday_gatekeeper.py#L106-L114)

```python
polarity_flipped = False
if direction == "Long"  and whale_cvd_ema_1h < 0: polarity_flipped = True
elif direction == "Short" and whale_cvd_ema_1h > 0: polarity_flipped = True

if stop_proximity_hit or polarity_flipped:
    return cls._create_response("Close", priority=10, confidence=9, llm_auth=True)
```

The comment above it says *"If `whale_cvd_ema_1h` completely flips polarity against the trade direction"* —
but the code tests the **sign of the level**, not a flip. `whale_cvd_ema_1h` is a signed cumulative share
count; for any symbol with net large-print selling since session open it is negative essentially all day,
regardless of what price is doing.

So: open a long, and the very next gatekeeper tick emits `Close` with confidence 9/10 and burns an LLM call.
Combined with A-3 (whale CVD frozen at exactly 0 on day 2 — and `0 < 0` is False), the check is
*either* always-on or always-off, never actually informative.

**Fix** — test an actual reversal with a magnitude gate, normalised to the instrument:

```python
prev = position.get("whale_cvd_at_entry", 0.0)
notional = max(1.0, ltp * float(raw_payload.get("avg_session_volume", 1.0)))
delta_norm = (whale_cvd_ema_1h - prev) * ltp / notional
adverse = (direction == "Long" and delta_norm < -FLIP_THRESHOLD) or \
          (direction == "Short" and delta_norm >  FLIP_THRESHOLD)
```

and record `whale_cvd_at_entry` when the position is saved
([api_server.py:145](trading_copilot/api_server.py#L145)).

---

### 🟠 A-7. Playbook generation can deadlock the entire API gateway

[reasoning_engine.py:563-573](trading_copilot/reasoning_engine.py#L563-L573)

```python
from data_services.upstox_feed import UpstoxAuthenticator
auth = UpstoxAuthenticator()
access_token = await auth.get_valid_token()          # <-- inside process 4
```

If `upstox_token.json` is older than 24 h, `get_valid_token()` falls through to `_manual_fallback()`
([upstox_feed.py:154-164](trading_copilot/data_services/upstox_feed.py#L154-L164)), which calls **blocking
`input()`** on line 159. `generate_playbook` is dispatched as a fire-and-forget task on the main event loop
([api_server.py:233](trading_copilot/api_server.py#L233)), so this blocks the entire process 4 loop:
the WebSocket stops, `/state` polling stops, the gatekeeper stops — with no error surfaced to the browser.

Two secondary problems in the same function:

1. It re-runs the **whole-market F&O screener** from process 4
   ([:566-567](trading_copilot/reasoning_engine.py#L566-L567)), duplicating process 1's Upstox quota and
   issuing ~180 sequential historical calls.
2. The enrichment loop is broken: `active_states.get(token, {})` uses the numeric exchange token from
   `get_all_fno_equities` ([:580-586](trading_copilot/reasoning_engine.py#L580-L586)), but `active_states`
   is keyed by `NSE_EQ|SYMBOL`. So `microstructure_available` is always `False` and `obi`/`cvd` are always
   the string `"N/A"` in the playbook payload sent to the LLM.

**Fix:** never authenticate from process 4. Add a `GET /api/token` endpoint on process 1 (which already owns
the token lifecycle) and have process 4 fetch it; fail fast with a clear UI error if it is absent. Separately,
gate `_manual_fallback` behind `sys.stdin.isatty()` so it can never block a server process.

---

### 🟠 A-8. `SignalLedger.record_signal` raises whenever a position is held

[reasoning_engine.py:106-107](trading_copilot/reasoning_engine.py#L106-L107) deliberately nulls the geometry
when the user holds a position:

```python
tactical_payload["math_setup"]["execution_geometry"] = None
tactical_payload["math_setup"]["expectancy_matrix"]  = None
```

[signal_ledger.py:53-55](trading_copilot/signal_ledger.py#L53-L55) then does:

```python
"padded_stop": math_setup.get("execution_geometry", {}).get("padded_stop", 0.0),
```

`.get(key, default)` returns the **stored `None`**, not the default — the default only applies when the key
is *absent*. So this is `None.get(...)` → `AttributeError`.

`record_signal` is reached exactly on the `CLOSE_EXISTING` / `REVERSE_POSITION` directives
([reasoning_engine.py:380-391](trading_copilot/reasoning_engine.py#L380-L391)) — which by definition only
occur when a position exists. The exception is swallowed by the outer handler
([:416-420](trading_copilot/reasoning_engine.py#L416-L420)) and surfaces to the user as
`"Error generating report: ..."`.

**Net effect: position-management signals are never recorded**, so the feedback loop measures entry signals
only. **[unverified — needs runtime]** for the exact traceback, but the code path is unambiguous.

**Fix:**

```python
geo = math_setup.get("execution_geometry") or {}
exp = math_setup.get("expectancy_matrix") or {}
```

(the same `or {}` idiom is already used correctly at
[intraday_gatekeeper.py:135,138](trading_copilot/intraday_gatekeeper.py#L135) — it just wasn't applied here).

---

### 🟠 A-9. Two data paths resolve to a directory that does not exist

Executed against this repo — `AlgoTrade/data/` **does not exist**; the data lives in
`AlgoTrade/trading_copilot/data/`.

| Module | Line | Expression | Resolves to |
|---|---|---|---|
| `rolling_state_engine.py` | [:117](trading_copilot/rolling_state_engine.py#L117) | `dirname(dirname(abspath(__file__)))/data` | `AlgoTrade/data` ❌ |
| `derivatives_worker.py` | [:16-17](trading_copilot/derivatives_worker.py#L16-L17) | `dirname(dirname(abspath(__file__)))/data` | `AlgoTrade/data` ❌ |

Both files use the correct `dirname(__file__)/data` form **elsewhere in the same file**
([rolling_state_engine.py:31,316](trading_copilot/rolling_state_engine.py#L31),
[derivatives_worker.py:63](trading_copilot/derivatives_worker.py#L63)) — so this is a copy-paste
inconsistency, not a deliberate layout choice.

**Consequences:**

1. `RollingStateEngine._load_parquet_metrics` ([:114-139](trading_copilot/rolling_state_engine.py#L114-L139))
   silently loads nothing — the `os.path.exists` check just fails for all 27 symbols. Its output,
   `daily_metrics_cache`, is in any case **written and never read** anywhere in the codebase, and is
   serialised into the 6.29 MB `cache_state.json` on every 5-minute save.
2. `derivatives_worker.get_historical_iv` ([:19-48](trading_copilot/derivatives_worker.py#L19-L48))
   always returns `(999.0, 0.0)`, so **IV Rank is never computed from the parquet 252-day series** as
   designed. It silently degrades to the `macro_baselines.json` fallback
   ([:59-77](trading_copilot/derivatives_worker.py#L59-L77)), which happens to use the *correct* path — so
   a value still appears in the UI and nothing looks broken.

A related but different problem: `playbook_state.json` is addressed by the **CWD-relative** literal
`os.path.join("trading_copilot", "playbook_state.json")`
([api_server.py:433](trading_copilot/api_server.py#L433),
[reasoning_engine.py:651](trading_copilot/reasoning_engine.py#L651)). That works when launched from the repo
root as the README instructs, and **fails silently** when launched from `trading_copilot/` as
[start_all.bat](trading_copilot/start_all.bat) does with `-d .`.

**Fix** — one module-level constant, imported everywhere:

```python
# trading_copilot/paths.py
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
SIGNALS_DIR = DATA_DIR / "signals"
PLAYBOOK_PATH = BASE_DIR / "playbook_state.json"
WATCHLIST_PATH = BASE_DIR / "watchlist.csv"
TOKEN_PATH = BASE_DIR / "upstox_token.json"
```

Then delete every ad-hoc `dirname(...)` chain. A one-line startup assertion (`assert DATA_DIR.is_dir()`)
would have caught this class of bug immediately.

---

### 🟠 A-10. LLM price output reaches the UI with no validation

The system prompt states explicit adjustment bounds
([reasoning_engine.py:233-240](trading_copilot/reasoning_engine.py#L233-L240)) — stop widened at most
1×ATR₁₅ₘ, target reduced at most 0.5×ATR₁₅ₘ, entry within ±0.3 % of `calculated_entry`.

**Nothing enforces them.** The values are read straight out of the model's JSON and placed into the UI card
([:354-366](trading_copilot/reasoning_engine.py#L354-L366)):

```python
risk_params = ticket.get("risk_parameters") or {}
ui_data = {
    "Entry_Target_Price": risk_params.get("final_entry",  0.0),
    "Stoploss":           risk_params.get("final_stop",   0.0),
    "Exit_Target_Price":  risk_params.get("final_target", 0.0),
    ...
}
```

A hallucinated decimal or a stop on the wrong side of entry is displayed as an actionable price with a
confidence score attached. There is also no sanity check that `final_stop < final_entry < final_target`
for a long (or the inverse for a short).

**Fix** — clamp server-side before the values ever leave `analyze_stock`:

```python
def _clamp_ticket(risk_params, geo, atr15, bias):
    entry = float(risk_params.get("final_entry") or geo["calculated_entry"])
    stop  = float(risk_params.get("final_stop")  or geo["padded_stop"])
    tgt   = float(risk_params.get("final_target") or geo["calculated_target"])

    entry = min(max(entry, geo["calculated_entry"] * 0.997),
                        geo["calculated_entry"] * 1.003)
    lo, hi = sorted((geo["padded_stop"] - 1.0 * atr15,
                     geo["padded_stop"] + 0.5 * atr15))
    stop = min(max(stop, lo), hi)

    if bias == "LONG"  and not (stop < entry < tgt): return geo, "LLM_GEOMETRY_REJECTED"
    if bias == "SHORT" and not (tgt < entry < stop): return geo, "LLM_GEOMETRY_REJECTED"
    return {"final_entry": entry, "final_stop": stop, "final_target": tgt}, None
```

On rejection, fall back to the deterministic `math_setup` geometry and tag the card so the operator can see
the model was overridden.

---

### 🟠 A-11. Three alert endpoints the UI depends on do not exist

The UI polls and renders an alert bell, unread badge and alert tray:

| UI call | Line |
|---|---|
| `GET /api/alerts/unread` | [index.html:1274](trading_copilot/templates/index.html#L1274) |
| `GET /api/alerts/history` | [index.html:1285+](trading_copilot/templates/index.html#L1285) |
| `POST /api/alerts/mark-read/{id}` | [index.html:1321+](trading_copilot/templates/index.html#L1321) |

`api_server.py` defines **28 routes; none of them match** (verified by grep — the string `alerts` does not
appear in the file). Meanwhile `ReasoningEngine.global_alerts` is faithfully populated on every actionable
verdict ([reasoning_engine.py:393-405](trading_copilot/reasoning_engine.py#L393-L405), capped at 50) and
**never exposed**.

The failure is silent: FastAPI returns `{"detail":"Not Found"}`, `data.count` is `undefined`,
`undefined > 0` is `false`, and the badge simply stays hidden
([index.html:1277-1282](trading_copilot/templates/index.html#L1277-L1282)). The operator sees a permanently
empty alert tray and reasonably concludes there were no alerts.

**Fix** — ~12 lines:

```python
@app.get("/api/alerts/unread")
async def alerts_unread():
    return {"count": sum(1 for a in ReasoningEngine.global_alerts if not a["read"])}

@app.get("/api/alerts/history")
async def alerts_history():
    return {"status": "success", "alerts": ReasoningEngine.global_alerts}

@app.post("/api/alerts/mark-read/{alert_id}")
async def alerts_mark_read(alert_id: int):
    for a in ReasoningEngine.global_alerts:
        if a["id"] == alert_id:
            a["read"] = True
    return {"status": "success"}
```

Same class of bug: `POST /api/map-option-tokens` ([api_server.py:121](trading_copilot/api_server.py#L121))
proxies to port 8001, which has no such route — that endpoint only ever existed in the dead
`smart_api_feed.py`. The UI button at [index.html:1821](trading_copilot/templates/index.html#L1821) is dead.

---

### 🟠 A-12. No WebSocket reconnection — a dropped stream is silent and permanent

[upstox_feed.py:308-309](trading_copilot/data_services/upstox_feed.py#L308-L309)

```python
def _on_close(self, code, reason):
    logger.warning(f"Streamer Closed: Code {code}, Reason: {reason}")
```

That is the entire handler. The three streams run on bare `threading.Thread(...).start()` calls
([:334-336](trading_copilot/data_services/upstox_feed.py#L334-L336)) with no supervision, no restart, and
no liveness check.

If the equity stream drops mid-session, `phantom_candles` simply stop updating. `calculate_technicals_loop`
keeps running against the last known phantom, `TerminalDashboard.active_states` keeps publishing, the
WebSocket keeps streaming to the browser, and the UI keeps showing a green **"Connected Live"** dot
([index.html:617-619](trading_copilot/templates/index.html#L617-L619)) — because that dot reflects the
*browser↔gateway* socket, not the *gateway↔exchange* one.

**The operator has no way to tell that market data has stopped.** Decisions continue to be computed on a
frozen snapshot.

**Fix** — supervise, and propagate staleness end-to-end:

```python
async def _supervise(self, name, streamer, keys, mode):
    backoff = 1
    while True:
        t = threading.Thread(target=streamer.connect, daemon=True)
        t.start()
        while t.is_alive():
            await asyncio.sleep(1)
        logger.error(f"{name} stream died — reconnecting in {backoff}s")
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)
```

and stamp `last_tick_ts` per token in `process_tick`, surface `data_age_seconds` in the `/state` payload,
and have the UI show an amber **STALE** banner when it exceeds ~15 s during market hours.

---

### 🟡 A-13. One bad symbol silently drops every symbol after it in the cycle

[rolling_state_engine.py:247-424](trading_copilot/rolling_state_engine.py#L247-L424)

```python
while True:
    try:
        for token, phantom in list(self.phantom_candles.items()):
            ...                                  # ~170 lines of per-symbol work
    except Exception as e:                       # :423  OUTSIDE the for-loop
        logger.error(f"Error in lazy calculator loop: {e}")
    await asyncio.sleep(1.5)
```

The `try` opens before the `for` and the `except` closes after it, so any unhandled exception on symbol *k*
aborts symbols *k+1 … n* for that cycle. If the fault is deterministic (a malformed DataFrame, a symbol with
too few bars), those symbols are starved **permanently** while the log shows a single repeating line.

There *is* a narrower guard around `MathEngine` ([:291-293](trading_copilot/rolling_state_engine.py#L291-L293))
that correctly `continue`s, but it covers only that one call — the ~130 lines after it are unprotected.

**Fix:** move the `try`/`except` inside the `for`, `continue` on error, and count consecutive failures
per symbol so a persistently broken symbol is quarantined and reported rather than retried 40 times a minute.

---

### 🟡 A-14. `parquet_engine` duplicates rows on every incremental sync

[data_services/parquet_engine.py:99-167](trading_copilot/data_services/parquet_engine.py#L99-L167)

For an existing file it fetches **5 days** of candles ([:99](trading_copilot/data_services/parquet_engine.py#L99)):

```python
days_back = 1500 if not os.path.exists(file_path) else 5
```

then de-duplicates against exactly **one** date ([:166](trading_copilot/data_services/parquet_engine.py#L166)):

```python
df = df[df['Date'].dt.date != new_df['Date'].iloc[0].date()]   # only the FIRST of the 5
df = pd.concat([df, new_df], ignore_index=True)                # appends all 5
```

So each sync adds ~4 duplicate rows. Repeated syncs compound them, corrupting every rolling statistic
computed from these files (`historical_vol_20d`, the volume profile, CAPM β).

**[unverified — needs runtime]**: the parquets I inspected show `dupes: 0`, so this endpoint evidently has
not been run repeatedly against them yet. The defect is in the code path, not (yet) in the data.

**Fix:**

```python
new_dates = set(new_df['Date'].dt.date)
df = df[~df['Date'].dt.date.isin(new_dates)]
df = pd.concat([df, new_df], ignore_index=True).sort_values('Date').reset_index(drop=True)
```

---

### 🟡 A-15. Screener's "volume shock" term is a constant for most stocks

[screener_engine.py:36-40](trading_copilot/screener_engine.py#L36-L40)

```python
vol_shock = (live_volume / vol_20ma) * 35
magnitude_score += min(vol_shock, 35)
```

Any stock trading at or above its 20-day average volume — i.e. roughly half the universe on any given day —
scores the full 35 points. The term saturates at ratio 1.0 and cannot distinguish a 1.1× day from a 5× day.
Since 35 is the largest single component of a ~135-point scale, the screener's ranking is dominated by a
term carrying almost no information.

Two further issues in the same scorer:

- **Scale mismatch.** `net_polarity += trend_dist * 100` vs `macd_dist * 1000` vs `comp_rs` (already a
  percentage) ([:48,55,82](trading_copilot/screener_engine.py#L48)). The MACD term is ~10× the others and
  effectively sets the LONG/SHORT bias alone.
- **Dead news branch.** [:61-72](trading_copilot/screener_engine.py#L61-L72) reads
  `news_data.get('sentiment')` and `.get('impact')`, but `NewsEngine` only ever writes `{"raw_news": [...]}`
  ([news_engine.py:154-161](trading_copilot/news_engine.py#L154-L161)). Both lookups always hit their
  defaults, so every symbol with a cache entry — *including one with an empty `raw_news` list* — receives a
  flat +5 and zero polarity contribution.
- **Mislabelled level.** `prev_day_high`/`prev_day_low` use `iloc[-1]`
  ([:87-88](trading_copilot/screener_engine.py#L87-L88)) — that is the **latest** bar, not the previous day.

**Fix** for the volume term — score the excess, not the level:

```python
vol_ratio = live_volume / max(vol_20ma, 1)
magnitude_score += min(max(0.0, (vol_ratio - 1.0)) * 17.5, 35)   # 3x volume -> full marks
```

and normalise every polarity contribution to a common z-scale before summing.

---

## §B — False and unimplemented claims

Claims are quoted from [README.md](../../README.md) and
[QuantFlow_architecture_summary.md](../../QuantFlow_architecture_summary.md).

### B.1 Verdicts

| # | Claim | Source | Verdict | Evidence |
|---|---|---|---|---|
| 1 | "**zero lock contention**" / "Each piece of shared state has exactly one writer" | README §Single-Writer Concurrency | ❌ **False** | `active_states` has ≥3 writers in proc 1; `phantom_candles` written by 3 OS threads and read by the asyncio loop mid-`pd.concat`; `RegimeManager`/`ConvictionScorer` mutated by 2 loops (§A-4). No mutexes exist — so there is no *contention*, but there is no single writer either. |
| 2 | Critical path "**completely allocation-free**" | README §Layer 1 | ❌ **False** | `process_tick` allocates per tick: `pd.to_datetime` + tz conversion ([:145](trading_copilot/rolling_state_engine.py#L145)), `bids`/`asks` lists ([upstox_feed.py:276-283](trading_copilot/data_services/upstox_feed.py#L276-L283)), `tick_dict` ([:223-229](trading_copilot/rolling_state_engine.py#L223-L229)), a fresh micro payload dict, and `np.polyfit` ([microstructure_engine.py:128](trading_copilot/microstructure_engine.py#L128)). |
| 3 | "**OAuth2 PKCE**" (Tech Stack table + `auth_manager.py` description) | README | ❌ **False, twice** | (a) No `code_verifier` / `code_challenge` anywhere — verified by grep. The flow is standard Authorization Code **with client secret** ([upstox_feed.py:166-195](trading_copilot/data_services/upstox_feed.py#L166-L195)). (b) `auth_manager.py` is **Angel One SmartAPI**, not Upstox, and is dead code. |
| 4 | "`macro_worker.py` … **or runs embedded from `upstox_feed.py`**" | arch summary §2.4 | ❌ **False** | `upstox_feed.py` never imports `macro_worker`. `macro_poller_loop` runs only under `if __name__ == "__main__"` ([macro_worker.py:108](trading_copilot/data_services/macro_worker.py#L108)). Forget to launch it and FII/DII is permanently 0. |
| 5 | "headless Playwright **or** manual fallback" | arch summary §2.2 | ❌ **False** | Headless is hard-disabled: `code = None` at [upstox_feed.py:205-207](trading_copilot/data_services/upstox_feed.py#L205-L207). Only the blocking `input()` path runs. |
| 6 | "**13-signal** weighted composite" | README §Layer 5, arch §3.1 | ⚠️ **Overstated** | 11 signals are read; the 12th (catalyst) is hardcoded `0.0` ([conviction_scorer.py:141](trading_copilot/conviction_scorer.py#L141)) and there is no 13th. Worse — see §C-1, its weight is *still applied*, capping the achievable composite. |
| 7 | "**hysteresis-protected** FSM" | README §Layer 4 | ⚠️ **Implemented but defeated** | Real code at [regime_manager.py:123-128](trading_copilot/regime_manager.py#L123-L128), but the buffer is filled at 2 Hz by the display path (§A-4), so the 3-sample window spans 1.5 s. Also it is 2-of-3, not 3-consecutive. |
| 8 | 5 market regimes | README §Layer 4 | ⚠️ **2 unreachable** | `PRE_BREAKOUT_SQUEEZE` and `MEAN_REVERSION_IMMINENT` can never fire (§A-5). |
| 9 | "Debounce Guard — requires **3 consecutive stable ticks**" | README §Layer 5 | ✅ **True** | [reasoning_engine.py:468-477](trading_copilot/reasoning_engine.py#L468-L477). Correctly implemented, ~30 s of stability. |
| 10 | "Anti-Cold-Start Hydration" | README, arch §9.5 | ✅ **True** | [rolling_state_engine.py:74-112](trading_copilot/rolling_state_engine.py#L74-L112). Caveat: 24 h wall-clock, not session-aware. |
| 11 | "Tri-Stream WebSocket Multiplexing" | README, arch §2.2 | ✅ **True** | [upstox_feed.py:321-338](trading_copilot/data_services/upstox_feed.py#L321-L338). Caveat: no reconnect (§A-12); the options stream's output is never read. |
| 12 | Feedback calibration loop | README, arch §3.3 | ✅ **True** | Ledger → analyzer → weights ([conviction_scorer.py:18-40](trading_copilot/conviction_scorer.py#L18-L40)) + prompt ([reasoning_engine.py:270-287](trading_copilot/reasoning_engine.py#L270-L287)). Caveats in §C-3. |
| 13 | "`pipeline_guard.py` — **circuit breaker & safety checks**" | README structure | ❌ **False** | It is a 34-line market-hours clock ([pipeline_guard.py](trading_copilot/pipeline_guard.py)). No breaker, no retry, no failure detection. |
| 14 | "`warm_layer_engine.py` — **historical warmup data fetcher**" | README structure | ❌ **False** | It reads local parquet files, fetches nothing, and is **dead code** (only `stitching_engine` imports it, which nothing imports). |
| 15 | "`stitching_engine.py`", "`websocket_engine.py`" listed as live components | README structure | ❌ **Dead** | Nothing imports `stitching_engine`; `websocket_engine` is Angel One code imported only by the dead `smart_api_feed.py`. |
| 16 | "`news_engine.py` — News catalyst analysis (**Gemini sentiment**)" | README structure | ⚠️ **Half true** | Per-symbol Gemini sentiment (`analyze_catalyst`, [:44-65](trading_copilot/news_engine.py#L44-L65)) is **never called**. Only *macro* sentiment uses Gemini. Symbol news reaches the LLM as raw headlines. |
| 17 | "`derivatives_engine.py` — BSM IV solver, **PCR, Max Pain**" | README structure | ⚠️ **Half true** | Only `implied_volatility()` is live. `OptionsAnalyzer`'s PCR/Max-Pain path is unreachable — `start_polling` is never called and it depends on a stub returning `[]`. |
| 18 | "`macro_eod_engine.py` — End-of-day macro metrics" | README structure | ❌ **Dead** | Imported once as `LegacyTracker` and never used ([rolling_state_engine.py:11](trading_copilot/rolling_state_engine.py#L11)). |
| 19 | Derivatives poller "**2-min interval**" | arch §2.2, README | ⚠️ **Misleading** | 29 targets × (4 sequential REST calls + 1 s sleep) exceeds 120 s before the trailing `sleep(120)`. Real cadence ≈ 4–6 min. **[unverified — needs runtime]** for the exact figure. |
| 20 | Layer 2 computes "IV Rank (52W), OI Volume Shock" **every 1.5 s** | README §Layer 2 | ⚠️ **Misleading** | IVR comes from the ~4-min REST poller; OI shock is a static value read from `macro_baselines.json`. Neither is recomputed at 1.5 s. |
| 21 | ".env keys `UPSTOX_API_KEY` / `UPSTOX_API_SECRET`" | README §Configuration | ❌ **Wrong** | Code reads `UPSTOX_CLIENT_ID` / `UPSTOX_CLIENT_SECRET` ([upstox_feed.py:46-47](trading_copilot/data_services/upstox_feed.py#L46-L47)) and also needs `UPSTOX_MOBILE_NO`, which the README omits entirely. Following the README yields `client_id=None`. ([.env.example](.env.example) is correct — the README is not.) |
| 22 | "`main.py` … Loads saved playbook state" | arch §2.1 | ⚠️ **CWD-dependent** | Works from the repo root; silently fails from `trading_copilot/` (§A-9). |
| 23 | "**~15 institutional semantic states**" | README §Layer 3 | ⚠️ **Fewer in practice** | 4 of them are permanently pinned to defaults (§A-5), so ~11 vary. |
| 24 | "`api_server.py` (25+ endpoints)" | README structure | ✅ **True** | 28 routes. But 3 endpoints the UI *calls* are missing (§A-11), and 3 that exist have no UI consumer. |
| 25 | "Performance Analyzer … win rates, profit factor, regime accuracy" | README, arch §3.3 | ⚠️ **No UI** | The three `/api/performance/*` endpoints exist ([api_server.py:161-183](trading_copilot/api_server.py#L161-L183)) but `index.html` never calls them. The analytics are computed and invisible. |
| 26 | UI "Auto-Analyze frequency in seconds" | [index.html:2673-2683](trading_copilot/templates/index.html#L2673-L2683) | ❌ **False confirmation** | `interval` is accepted by `LoopStartRequest` and **never used** — `start_analysis_loop` only calls `set_llm_toggle` ([api_server.py:191-198](trading_copilot/api_server.py#L191-L198)). The real cadence is the fixed 10 s gatekeeper + debounce. The UI nonetheless pops `alert("Started auto-analyze for X every N seconds")`. |
| 27 | Alert bell / unread badge / alert tray | [index.html:75-91](trading_copilot/templates/index.html#L75-L91) | ❌ **Non-functional** | All three backing endpoints are absent (§A-11). |
| 28 | "Map Option Tokens" button | [index.html:1821](trading_copilot/templates/index.html#L1821) | ❌ **Non-functional** | Proxies to a route that does not exist on 8001. |
| 29 | `start_all.bat` as the launcher | repo root | ❌ **Broken** | References `smart_api_feed.py` (dead) and `nse_feed.py` (**does not exist in the repo**). The arch summary already flags this; it remains unfixed. |
| 30 | "`master_bootstrap.py` … rate limiting, 30-minute cooldown on HTTP 429" | arch §5.1 | ✅ **True** | [master_bootstrap.py:20-42](trading_copilot/scripts/master_bootstrap.py#L20-L42) — 1805 s. |

### B.2 Summary

Of 30 checked claims: **11 false**, **11 overstated or misleading**, **8 verified true**.

The pattern is consistent — the *concurrency and authentication* claims are marketing language that the code
does not support, the *module inventory* in the README describes several files by their intended purpose
rather than their actual (dead) state, and the *pipeline* claims are broadly accurate but describe designed
behaviour that specific bugs (§A-5, §A-4) silently disable.

---

## §C — Quantitative methodology weaknesses

### 🟡 C-1. `implied_probability` is a fabricated statistic, and the catalyst weight caps it

[conviction_scorer.py:265-272](trading_copilot/conviction_scorer.py#L265-L272)

```python
raw_prob    = 1 / (1 + math.exp(-(abs(composite) * 4.5)))
p_implied   = 0.50 + ((raw_prob - 0.50) * 0.85)
p_breakeven = effective_risk / (effective_risk + effective_reward)
stat_edge   = p_implied - p_breakeven
```

This is a logistic transform of a hand-weighted, hand-thresholded score. The constants 4.5 and 0.85 were not
fitted to anything — there is no calibration step anywhere in the repo. Yet the output is labelled
`implied_probability`, shipped to the LLM as `expectancy_matrix`, presented to the model as **ground truth**
("Treat … `math_setup.expectancy_matrix` … as ground truth",
[reasoning_engine.py:191-193](trading_copilot/reasoning_engine.py#L191-L193)), and used to compute the
`Confidence_Score` shown to the operator ([intraday_gatekeeper.py:147](trading_copilot/intraday_gatekeeper.py#L147)).

Two structural consequences:

**(a) The floor is 64 %.** Any setup that clears `abs(composite) ≥ 0.15` yields
`p_implied ≥ 0.5 + 0.85·(σ(0.675) − 0.5) ≈ 0.639`. The weakest admissible signal is reported as a ~64 %
probability trade.

**(b) The dead catalyst weight caps the ceiling.** `catalyst_norm` is hardcoded `0.0`
([:141](trading_copilot/conviction_scorer.py#L141)) but `w_cat` is **still multiplied in**
([:147](trading_copilot/conviction_scorer.py#L147)):

```python
composite = micro*w_micro + struct*w_struct + deriv*w_deriv + 0.0*w_cat
```

So 10–25 % of the weight budget is permanently dead. In `TREND_EXPANSION` (`w_cat = 0.25`) the maximum
achievable `abs(composite)` is 0.75, not 1.0 — every score is systematically deflated, and the deflation
varies by regime, making cross-regime scores non-comparable.

**Fix, part 1 (immediate)** — renormalise over the live weights only:

```python
active = {"micro": w_micro, "struct": w_struct, "deriv": w_deriv}
if catalyst_norm != 0.0: active["cat"] = w_cat
total = sum(active.values())
composite = (micro_norm*active["micro"] + struct_norm*active["struct"]
             + deriv_norm*active["deriv"] + catalyst_norm*active.get("cat", 0.0)) / total
```

**Fix, part 2 (the real one)** — replace the invented sigmoid with an empirically fitted probability. The
data to do this already exists: `SignalLedger` records `composite_score` alongside
`directional_correct_30m` for every signal. Once a few hundred are accumulated, fit a one-feature logistic
regression offline and load the coefficients:

```python
# calibration.json, regenerated nightly from data/signals/*.jsonl
# p = 1 / (1 + exp(-(b0 + b1 * composite)))
p_implied = 1.0 / (1.0 + math.exp(-(CAL["b0"] + CAL["b1"] * abs(composite))))
```

Until enough data exists, report `p_implied = None` and let `stat_edge` gate on R:R alone. A stated
"unknown" is far more useful than a fabricated 64 %.

**Related:** because `p_breakeven` is typically ~0.35–0.45 with the 1.5×ATR minimum reward, `stat_edge` is
usually ~0.2 — comfortably above the 0.05 rejection threshold
([:274](trading_copilot/conviction_scorer.py#L274)). **The `stat_edge` gate therefore almost never rejects
anything**; the only effective filter is `abs(composite) ≥ 0.15`.

### 🟡 C-2. The 5-year Value Area algorithm is wrong

[macro_bootstrap.py:143-155](trading_copilot/scripts/macro_bootstrap.py#L143-L155)

```python
vol_profile = vol_profile.sort_values(by='Volume', ascending=False)   # sort ALL bins by volume
for _, row in vol_profile.iterrows():
    accumulated_vol += row['Volume']
    value_area_bins.append(row['price_bin'])
    if accumulated_vol >= target_vol: break
val_low  = min(value_area_bins)      # min/max of a NON-CONTIGUOUS set
val_high = max(value_area_bins)
```

The standard Value Area is built by expanding **contiguously outward from the POC**, adding the heavier of
the two adjacent bins until 70 % of volume is enclosed. This code instead picks the globally heaviest bins
anywhere in the range and takes their min/max — producing a span far wider than the true value area.

Confirmed in the generated data:

| Symbol | `volume_poc_price` | `value_area_low` | `value_area_high` | Span |
|---|---|---|---|---|
| 360ONE | 1144 | **331** | 1202 | 263 % of POC |
| ADANIENSOL | 767 | 601 | **1708** | 144 % of POC |
| ADANIENT | 2400 | 1195 | 3835 | 110 % of POC |

A "value area" spanning 331→1202 conveys no information.

The irony is that **the correct algorithm already exists in this codebase** —
[`calc_volume_profile_high_fidelity`](trading_copilot/technical_engine.py#L365-L377) does proper contiguous
expansion. `macro_bootstrap` should call it rather than reimplement it incorrectly.

Two lesser issues in the same function: the profile is built from **daily closes** (`df['Close']`), smearing
a whole day's volume into one price bin — typical-price-per-bar or an intraday profile would be far more
faithful; and these `structural_liquidity_5y` levels do reach the flat payload
([rolling_state_engine.py:328-330](trading_copilot/rolling_state_engine.py#L328-L330)) even though
`SemanticTagger` uses the (correct) `rolling_20d_*` levels for proximity.

### 🟡 C-3. The feedback loop measures a biased sample with a trivial success criterion

Four independent problems:

**(a) Selection bias.** Only `CONFIRM`/`ADJUST` verdicts with actionable directives are recorded
([reasoning_engine.py:380-391](trading_copilot/reasoning_engine.py#L380-L391)). `ABORT` and `DEFER` are never
logged, so there is no counterfactual — you cannot tell whether the LLM's rejections were correct, and the
"win rate" measures only *LLM-confirmed* signals, not the deterministic pipeline's discrimination.

**(b) A +0.001 % move counts as a win.** [signal_ledger.py:131](trading_copilot/signal_ledger.py#L131):

```python
record["outcome"]["directional_correct_30m"] = record["outcome"]["pnl_30m_pct"] > 0
```

With no threshold, this converges on ~50 % from pure noise plus whatever drift exists. It should require
the move to clear costs and a meaningful fraction of ATR:

```python
threshold = max(0.15, 0.25 * atr_pct_at_signal)     # percent
outcome["directional_correct_30m"] = outcome["pnl_30m_pct"] > threshold
```

**(c) Stop/target hits are sampled once a minute.** The resolver polls at 60 s
([:162](trading_copilot/signal_ledger.py#L162)) and compares only the instantaneous LTP
([:111-125](trading_copilot/signal_ledger.py#L111-L125)). Any stop touched and recovered inside a minute is
missed, so `stop_hit_rate` and `target_hit_rate` are systematically understated — the recorded PnL is
optimistic relative to what a real stop would have produced. The resolver should read the 5-minute bar's
high/low from `ltf_df` rather than sampling the last price.

**(d) Pending signals are lost on restart.** `_pending_signals` is in-memory only
([:11](trading_copilot/signal_ledger.py#L11)). Any signal younger than 60 minutes at shutdown is never
resolved and stays `PENDING` in the JSONL forever — and `_compute_metrics` filters to
`RESOLVED`/`RESOLVED_EARLY` ([performance_analyzer.py:14](trading_copilot/performance_analyzer.py#L14)), so
those signals silently vanish from the statistics. For a system restarted daily, this drops every signal from
the last hour of each session — disproportionately the closing-hour signals.

**Fix:** rebuild `_pending_signals` on startup by scanning the last two days of JSONL for
`status == "PENDING"`.

**(e) "Profit factor" is not a profit factor.**
[performance_analyzer.py:21-24](trading_copilot/performance_analyzer.py#L21-L24) sums *percentage moves*,
ignoring position size, the actual stop, and costs. It is a gross-percentage ratio; calling it profit factor
and feeding it to the LLM as a calibration input ([reasoning_engine.py:279](trading_copilot/reasoning_engine.py#L279))
overstates its meaning.

### 🟡 C-4. Several indicators are dimensionally or structurally unsound

| Issue | Location | Problem |
|---|---|---|
| **`whale_cvd_slope > 50` is an absolute threshold** | [semantic_tagger.py:67](trading_copilot/semantic_tagger.py#L67) | The slope is in *shares per sample*. For SUZLON (crores of shares) 50 is noise; for OFSS (thousands) it is a huge move. The single most important gate in Block 1 is not comparable across the watchlist. **Fix:** normalise — `slope / (avg_session_volume/375)` or express in rupee notional. |
| **1h and 4h candles are midnight-anchored** | [technical_engine.py:411-412](trading_copilot/technical_engine.py#L411-L412) | `df.resample('4h')` yields 08:00–12:00 and 12:00–16:00 buckets. Against an NSE session of 09:15–15:30 that is a 2 h 45 m "4-hour" candle followed by a 3 h 30 m one. The 1h series has a 45-minute first bar. Every `*_1h` / `*_4h` indicator is computed on irregular bars. **Fix:** `df.resample('4h', origin='09:15')` (and `'1h'` likewise), or resample on a session-relative bar index. |
| **"Double bottom" is detected on closes, not lows** | [technical_engine.py:139-151](trading_copilot/technical_engine.py#L139-L151) | `argrelextrema(closes, np.less, order=5)` — a double bottom is a structure in *lows*. Combined with a 0.2 % equality tolerance it will rarely fire, and when it does it is measuring the wrong series. It nonetheless feeds `high_probability_setup` ([rolling_state_engine.py:411-416](trading_copilot/rolling_state_engine.py#L411-L416)). |
| **H&S has no neckline break or volume confirmation** | [technical_engine.py:161-174](trading_copilot/technical_engine.py#L161-L174) | Three peaks in the right shape is a *candidate*, not a pattern. Without a neckline break the flag is not actionable. |
| **`rolling_20d_*` is computed over 30 days** | [rolling_state_engine.py:358](trading_copilot/rolling_state_engine.py#L358) | `temp_df` is the 30-day `ltf_df` ([historical_engine.py:139](trading_copilot/historical_engine.py#L139)). The name says 20 d; the LLM prompt reasons about it as a 20-day level ([reasoning_engine.py:207](trading_copilot/reasoning_engine.py#L207)). Either slice to 20 days or rename. |
| **POC binned at fixed ₹1** | [microstructure_engine.py:21-22](trading_copilot/microstructure_engine.py#L21-L22) | For YESBANK/IDEA (~₹20) a ₹1 bin is ~5 % of price — hopelessly coarse. For OFSS (~₹10,000) it is over-fine and creates thousands of near-empty bins. **Fix:** tie the bin to tick size or a fraction of ATR, as `macro_bootstrap` already does with `get_tiered_step_size`. |
| **CAPM alpha omits the risk-free rate on the market leg** | [macro_bootstrap.py:216](trading_copilot/scripts/macro_bootstrap.py#L216) | `alpha = r_s − (rf + β·r_m)`; correct CAPM is `r_s − (rf + β·(r_m − rf))`. The variable is named `alpha_simplified` and the arch summary says "Simplified Alpha", so this is disclosed — but the resulting figure is biased by `β·rf` (≈7 % × β annually) and is not comparable to a published alpha. |
| **Two different IV solvers with different failure semantics** | [derivatives_engine.py:11-57](trading_copilot/derivatives_engine.py#L11-L57) vs [derivatives_worker.py:84-105](trading_copilot/derivatives_worker.py#L84-L105) | The first returns `np.nan` on non-convergence (correct); the second returns `max(sigma, 0.0)` — **the last un-converged iterate**, silently emitting a garbage IV. The second also seeds `sigma=0.3` vs `0.5` and uses `r=0.07` vs `0.10`. **Fix:** delete `calc_bsm_iv`, import `implied_volatility`, standardise `r`. |
| **ATM IV is call-side only** | [derivatives_worker.py:161-179](trading_copilot/derivatives_worker.py#L161-L179) | Only CE contracts are considered (`is_call=True`). Put-side IV is ignored, so skew is invisible and the estimate is noisier than the CE/PE average that `master_bootstrap` correctly uses ([:297-306](trading_copilot/scripts/master_bootstrap.py#L297-L306)). |
| **"Market Breadth" is watchlist breadth** | [api_server.py:343-353](trading_copilot/api_server.py#L343-L353), [rolling_state_engine.py:373-379](trading_copilot/rolling_state_engine.py#L373-L379) | A/D is computed over 27 self-selected symbols and labelled "Market Breadth" in the UI. It measures the watchlist, not the market — and the watchlist is chosen for expected movement, so it is a biased sample. The real NIFTY-50 A/D fetcher exists in the dead `macro_eod_engine.fetch_market_breadth` ([:96-137](trading_copilot/macro_eod_engine.py#L96-L137)). Either wire that up or relabel the UI "Watchlist A/D". |

---

## §D — Performance and architecture

### 🔴 D-1. The ledger is re-read from disk ~100 times per second

The worst performance defect in the system, and it compounds silently.

```
api_server WebSocket loop @ 2 Hz
  └─ for each of 27 symbols:                        api_server.py:356-375
       └─ build_structured_payload                  reasoning_engine.py:57
            └─ ConvictionScorer.score_setup         conviction_scorer.py:42
                 └─ _get_adaptive_weights           conviction_scorer.py:6
                      └─ PerformanceAnalyzer.get_feedback_payload(14)
                           ├─ SignalLedger.load_all_signals(14)   <- opens+parses up to 14 JSONL files
                           └─ compute_regime_accuracy(14)
                                └─ SignalLedger.load_all_signals(14)  <- AGAIN
```

`load_all_signals` opens and JSON-parses every daily log in the window
([signal_ledger.py:164-185](trading_copilot/signal_ledger.py#L164-L185)). There is **no caching at any
level**.

Arithmetic: 27 symbols × 2 Hz × 2 loads = **108 full ledger scans per second**, each touching up to 14 files.
That is ~1,500 file opens/second, on top of `macro_baselines.json` and `institutional_flow.json` being
re-parsed per symbol per 1.5 s in process 1 (§D-2).

The cost grows with ledger size, so the system gets slower the longer it is used — the classic profile of a
bug that passes a short smoke test and degrades in production.

**Fix** — a TTL cache on the analyzer plus a single load per call:

```python
class PerformanceAnalyzer:
    _cache: dict = {}
    _cache_ts: float = 0.0
    _TTL = 300.0     # signal outcomes only change on 30/60-min boundaries

    @classmethod
    def get_feedback_payload(cls, last_n_days=14):
        now = time.monotonic()
        if cls._cache and (now - cls._cache_ts) < cls._TTL:
            return cls._cache
        signals = SignalLedger.load_all_signals(last_n_days)   # ONE load
        metrics = cls._compute_metrics(signals)
        regime_acc = cls._regime_accuracy_from(signals)         # reuse, don't reload
        ...
        cls._cache, cls._cache_ts = payload, now
        return payload
```

Fixing §A-4 (`advance_state`) removes the 2 Hz caller entirely, which alone is a ~20× reduction. Do both.

### 🟠 D-2. Disk I/O inside the hot calculation loop

[rolling_state_engine.py:316-320](trading_copilot/rolling_state_engine.py#L316-L320) and
[:370](trading_copilot/rolling_state_engine.py#L370) re-open and re-parse `macro_baselines.json` (20 KB) and
`institutional_flow.json` **per symbol, per 1.5-second cycle** — 27 × 0.67 Hz ≈ 18 file reads/second for data
that changes once a day.

**Fix:** load once at construction into a class attribute; add an `mtime` check every 60 s if hot-reload
matters.

### 🟠 D-3. Full recomputation of 1,650-row indicators to use one value

`generate_signal_payload` recomputes RSI, MACD, Bollinger, ATR, VWAP, CMF, the ToD z-score, five resampled
timeframes and a 100-bin volume profile across the **entire** 30-day history, then discards all but
`.iloc[-1]` ([technical_engine.py:504](trading_copilot/technical_engine.py#L504)). Per symbol. Every 1.5 s.

The two worst offenders:

```python
df['vol_z_score'] = df.apply(calc_tod_z, axis=1)      # :104  ~1,650 Python calls per symbol per cycle
...
for i in range(len(indices)):                          # :348  Python loop over ~1,650 rows
    vol_profile[indices[i]] += volumes[i]
```

At 27 symbols and 0.67 Hz that is ~30,000 row-wise Python invocations per second for a single scalar output,
plus a `pd.concat` full-frame copy ([rolling_state_engine.py:261](trading_copilot/rolling_state_engine.py#L261))
and two `df.copy()` calls ([technical_engine.py:481,535](trading_copilot/technical_engine.py#L481)) per symbol
per cycle.

**Fixes, in order of payoff:**

1. **Vectorise the volume profile** — `np.bincount(indices, weights=volumes, minlength=bins)` replaces the
   loop entirely (~100× faster).
2. **Vectorise the ToD z-score** — precompute the `time → (mean, std)` table once per session and `map` it:
   ```python
   stats = df['time'].map(tod_mean), df['time'].map(tod_std)
   df['vol_z_score'] = ((df['volume'] - stats[0]) / stats[1]).fillna(0.0)
   ```
3. **Cache the ToD baseline** — it is derived from history and changes only when a bar is committed.
4. **Slice before computing** — indicators need `max(period)·3` bars, not 1,650. Passing `df.tail(400)` to
   `calc_trend_and_momentum` is numerically indistinguishable for RSI-14/MACD-26/BB-20 and ~4× cheaper.
5. **Yield between symbols** — `await asyncio.sleep(0)` inside the `for` so the loop stops starving the
   `/state` endpoint (§D-4).

### 🟠 D-4. Blocking work on the event loop

| Site | Blocking operation |
|---|---|
| [rolling_state_engine.py:247-424](trading_copilot/rolling_state_engine.py#L247-L424) | all 27 symbols' technicals, one `await` at the end — process 1's `/state` endpoint stalls for the whole batch |
| [rolling_state_engine.py:68-69](trading_copilot/rolling_state_engine.py#L68-L69) | synchronous `json.dump` of a **6.29 MB** structure every 5 min |
| [api_server.py:369-375](trading_copilot/api_server.py#L369-L375) | full semantic + regime + conviction pipeline for 27 symbols, twice a second, inside the WebSocket send loop |
| [scrip_master_engine.py:53](trading_copilot/scrip_master_engine.py#L53) | first `_get_df()` parses the full NSE instrument master synchronously from an async context |
| [upstox_feed.py:159](trading_copilot/data_services/upstox_feed.py#L159) | `input()` (§A-7) |

**Fix:** `await asyncio.to_thread(...)` for the cache write and the scrip-master parse; `await asyncio.sleep(0)`
between symbols in both loops; and per §A-4, the WebSocket loop should do no scoring at all — it should
serialise a payload the gatekeeper already produced.

### 🟡 D-5. Cross-process design costs more than it buys

Two processes each hold a `TerminalDashboard`, and process 4's is replaced wholesale twice a second
([api_server.py:47-49](trading_copilot/api_server.py#L47-L49)) — ~320 KB/s of JSON encode+decode to move
data between processes on one machine, plus up to 500 ms of staleness before the gatekeeper sees a tick.

The split buys real isolation (a feed crash does not kill the UI) but the *implementation* undermines it:
process 4 authenticates to Upstox independently (§A-7) and runs its own market-wide screener, so the
processes are not actually decoupled.

**Fix (incremental, low risk):** keep the split, but make the transport honest —
replace `GET /state` polling with a server-sent-events or WebSocket push from 8001 → 8000 so updates are
event-driven, and add an `If-None-Match`/version field so unchanged symbols are not re-sent.

### 🟡 D-6. Miscellaneous

- `genai.Client(api_key=...)` is constructed on **every** LLM call
  ([reasoning_engine.py:176](trading_copilot/reasoning_engine.py#L176),
  [news_engine.py:50,276](trading_copilot/news_engine.py#L50)) — hoist to a module-level singleton.
- `derivatives_worker` writes `scratch/derivatives_debug.json` on every cycle
  ([:278-281](trading_copilot/derivatives_worker.py#L278-L281)) via a **CWD-relative** path, in production.
- `@app.on_event("startup")` ([api_server.py:82](trading_copilot/api_server.py#L82)) is deprecated in
  current FastAPI — use the `lifespan` context manager.
- `datetime.datetime.utcnow()` ([intraday_gatekeeper.py:42](trading_copilot/intraday_gatekeeper.py#L42),
  [microstructure_engine.py:60](trading_copilot/microstructure_engine.py#L60)) is deprecated; use
  `datetime.now(ZoneInfo("Asia/Kolkata"))` directly rather than hand-adding a 5:30 offset.
- `HistoryManager` rewrites the entire `trade_history.json` on every operation
  ([history_manager.py:36-41](trading_copilot/history_manager.py#L36-L41)).
- Bare `except: pass` at [api_server.py:50,64,79](trading_copilot/api_server.py#L50) hides every polling
  failure — including a feed that has been down for hours.

---

## §E — Security and operations

| # | Issue | Location | Risk |
|---|---|---|---|
| E-1 | **Gateway binds `0.0.0.0` with no authentication** | [api_server.py:442](trading_copilot/api_server.py#L442) | Every endpoint — watchlist mutation, LLM triggering, ledger writes, playbook generation — is reachable from any host on the LAN. Combined with `SERVER_IP = '192.168.29.123:8000'` in the UI, LAN exposure is clearly intentional; authentication is not. **Fix:** bind `127.0.0.1`, or add a shared-secret header check. |
| E-2 | **`allow_origins=["*"]` with `allow_credentials=True`** | [api_server.py:21-27](trading_copilot/api_server.py#L21-L27) | An invalid combination that browsers reject, and a permissive posture regardless. **Fix:** enumerate the actual origins. |
| E-3 | **A live-looking TOTP secret sits in `.env.example`, staged for commit** | [.env.example](.env.example) | `UPSTOX_TOTP_KEY=XHRGR2YOVCEGBYT3MQEBLI6CV3POJD7L` is a real-looking base32 secret, not a placeholder — every other value in the file is `your_*`. **Verified: the file is currently _untracked_, so it is not yet in git history** — but it is a new file sitting in the working tree of a repo whose other new files are clearly headed for a commit. If that string is the operator's actual seed, committing it is a full second-factor compromise. **Fix, before the next commit:** replace with `your_totp_secret`; rotate the Upstox TOTP seed if the value is genuine. |
| E-4 | **XSS sink on LLM- and news-derived text** | [index.html:2278](trading_copilot/templates/index.html#L2278), [:1965](trading_copilot/templates/index.html#L1965) | `data.Reason` (the LLM's `institutional_rationale`, itself derived from third-party news headlines) is written with `innerHTML`. 57 `innerHTML` sites exist. Low practical risk on localhost, but the input chain is genuinely external. **Fix:** `textContent` for all model- and API-derived strings. |
| E-5 | ~~`.env` is committed~~ — **checked, not an issue** | repo root | `.gitignore` covers `.env`, and `git ls-files` confirms it is untracked. No action needed. |
| E-6 | **`os._exit()` used for shutdown** | [main.py:19,30](trading_copilot/main.py#L19), [upstox_feed.py:483,567](trading_copilot/data_services/upstox_feed.py#L483) | Skips `finally` blocks and buffer flushes, so an in-flight `cache_state.json` or signal-log write can be truncated. |
| E-7 | **No tests at all** | — | Zero test files in the project. For a system computing trade parameters, the volume bug (§A-2) and the `None.get()` crash (§A-8) are exactly what a handful of unit tests would have caught. |
| E-8 | **Repo hygiene** | root | ~40 untracked scratch files (`test_*.py`, `script_test_*.js`, `temp.js`, a 1.9 MB `grep_transcript.txt`, `resume.tex`, `current_diff.txt`). `git status` is unusable as a signal. **Fix:** `.gitignore` + `scratch/`. |

---

## §F — Dead code

Full inventory in [Architecture_detailed_overview.md §9](Architecture_detailed_overview.md#9-module-inventory--live-vs-dead).
Summary: **~714 lines across 6 modules** (≈6.7 % of the codebase) plus **11 dead symbols inside live modules**.

Ranked by harm, not by size:

1. **`MTFFeatureExtractor`** ([mtf_extractor.py](trading_copilot/mtf_extractor.py)) — not merely unused;
   its absence disables 2 of 5 regimes and 4 of 11 conviction signals (§A-5). **This is a missing wire, not
   dead code — restore it.**
2. **`OptionsAnalyzer`** ([derivatives_engine.py:61-288](trading_copilot/derivatives_engine.py#L61-L288)) —
   imported in 4 files, so it *looks* live; its class-level defaults (`macro_state["pcr"] = 1.0`) are read as
   though they were data.
3. **`MetricsCalculator`** ([upstox_feed.py:374-378](trading_copilot/data_services/upstox_feed.py#L374-L378)) —
   returns hardcoded `{"pcr": 1.2, "max_pain": 25000}`. Harmless only because nothing calls it.
4. **Angel One stack** — `smart_api_feed.py`, `websocket_engine.py`, `auth_manager.py` (369 lines). Superseded
   by Upstox, still referenced by `start_all.bat` and the README.
5. **`stitching_engine.py` + `warm_layer_engine.py`** (185 lines) — an earlier "hot/warm layer" design,
   entirely orphaned.
6. **`macro_eod_engine.py`** (160 lines) — but see §C-4: its `fetch_market_breadth` is the *correct*
   implementation of a metric the live system currently fakes. Salvage that function before deleting.

---

## §G — Better approach — prioritised remediation

Ordered by (trading impact) ÷ (effort). Each item is independently shippable.

### Phase 1 — Stop showing wrong numbers (half a day)

| # | Change | Effort |
|---|---|---|
| 1 | **Delete the fabricated macro fallback** (§A-1) → render `UNAVAILABLE` | 10 min |
| 2 | **Fix phantom volume** — difference VTT once, in one place (§A-2) | 1 h |
| 3 | **Reset `last_vtt_state` and `vol_profile_state` daily** (§A-3) | 20 min |
| 4 | **Add `paths.py`**, replace every `dirname()` chain, assert `DATA_DIR` at startup (§A-9) | 1 h |
| 5 | **`or {}` in `record_signal`** (§A-8) | 5 min |
| 6 | **Add the 3 alert endpoints** (§A-11) | 20 min |
| 7 | **Fix the README `.env` block**; replace the TOTP seed in `.env.example` **before it gets committed** (§B-21, §E-3) | 15 min |

> After Phase 1, the numbers on screen mean what they say. Nothing before this point is worth optimising.

### Phase 2 — Restore designed behaviour (2–3 days)

| # | Change | Notes |
|---|---|---|
| 8 | **Wire `MTFFeatureExtractor` into the flat payload** (§A-5) | Unlocks 2 regimes + 4 conviction signals. Re-tune `calc_volatility_state` thresholds against real data afterwards. |
| 9 | **Split read from write: `advance_state` flag** (§A-4) | Restores real hysteresis, fixes `regime_age_epochs`, fixes the whipsaw shield, and removes ~95 % of the load in D-1. **Highest-leverage single change in the codebase.** |
| 10 | **Renormalise the composite over live weights** (§C-1a) | Makes scores comparable across regimes. |
| 11 | **Server-side clamp of LLM risk parameters** (§A-10) | |
| 12 | **Fix the whale-CVD polarity check** (§A-6) | Requires storing `whale_cvd_at_entry` on position save. |
| 13 | **Move `try`/`except` inside the per-symbol loop** (§A-13) | |
| 14 | **Supervise the 3 WebSocket threads + surface `data_age_seconds`** (§A-12) | Add a STALE banner to the UI. |
| 15 | **Never authenticate from process 4** — add `GET /api/token` on 8001; gate `input()` behind `isatty()` (§A-7) | |

### Phase 3 — Make the numbers defensible (1 week)

| # | Change | Notes |
|---|---|---|
| 16 | **Replace the invented `p_implied` with a fitted logistic** (§C-1) | Report `None` until ≥200 resolved signals. This converts the system's headline statistic from decoration into a measurement. |
| 17 | **Log `ABORT`/`DEFER` verdicts too** (§C-3a) | Without the counterfactual you cannot measure whether the LLM layer adds value over the deterministic layer — arguably the most important open question about this system. |
| 18 | **Threshold `directional_correct`** at cost + 0.25·ATR (§C-3b) | |
| 19 | **Resolve stops/targets from 5-min bar high/low**, not 60 s LTP samples (§C-3c) | |
| 20 | **Rebuild `_pending_signals` from JSONL on startup** (§C-3d) | |
| 21 | **Session-anchor the 1h/4h resample** (`origin='09:15'`) (§C-4) | |
| 22 | **Normalise `whale_cvd_slope`** by session volume (§C-4) | |
| 23 | **Reuse `calc_volume_profile_high_fidelity` in `macro_bootstrap`** (§C-2) | Delete the incorrect reimplementation; regenerate baselines. |
| 24 | **Unify the two IV solvers**; add put-side ATM IV (§C-4) | |
| 25 | **Relabel "Market Breadth" → "Watchlist A/D"**, or salvage `fetch_market_breadth` (§C-4) | |

### Phase 4 — Performance (2–3 days)

| # | Change | Expected gain |
|---|---|---|
| 26 | **TTL-cache `PerformanceAnalyzer`; one ledger load per call** (§D-1) | ~1,500 → ~0 file opens/sec |
| 27 | **Hoist `macro_baselines` / `institutional_flow` out of the hot loop** (§D-2) | 18 → 0 reads/sec |
| 28 | **`np.bincount` for the volume profile; vectorise the ToD z-score** (§D-3) | ~30k Python calls/sec eliminated |
| 29 | **Slice to `tail(400)` before indicator computation** (§D-3) | ~4× on the dominant cost |
| 30 | **`await asyncio.to_thread` for the 6 MB cache write and scrip-master parse** (§D-4) | removes two multi-second stalls |
| 31 | **`await asyncio.sleep(0)` between symbols** in both hot loops (§D-4) | `/state` stops stalling |
| 32 | **Module-level `genai.Client` singleton** (§D-6) | |

### Phase 5 — Hygiene and durability (ongoing)

| # | Change |
|---|---|
| 33 | Delete the Angel One stack, `stitching_engine`, `warm_layer_engine`; salvage `fetch_market_breadth` first (§F) |
| 34 | Rewrite `start_all.bat` to match reality, or replace with a `docker-compose` / supervisor config (§B-29) |
| 35 | Bind `127.0.0.1` or add a shared-secret header; fix the CORS combination (§E-1, §E-2) |
| 36 | `textContent` for all model- and API-derived strings in the UI (§E-4) |
| 37 | `.gitignore` the ~40 scratch files (§E-8) |
| 38 | Add a UI panel for the three orphaned `/api/performance/*` endpoints (§B-25) |
| 39 | Either honour the per-symbol `interval` or remove the control and its false confirmation (§B-26) |
| 40 | **Tests** — start with the ones that would have caught real bugs: VTT differencing, day-boundary resets, `record_signal` with a position, path resolution, geometry invariants (`stop < entry < target`), and a golden-payload test for `SemanticTagger` (§E-7) |

### A structural suggestion beyond the bug list

The recurring theme in §A-2, §A-3, §A-4 and §A-13 is that **state is scattered across module-level class
attributes with no ownership rules**, mutated from three OS threads and two event loops. The fixes above
address each symptom; the underlying design keeps generating them.

The lowest-risk structural improvement is to make the tick path a **queue** rather than a callback:

```python
# upstox_feed.py — WS threads only enqueue; they never touch shared state
def _on_market_update(self, message):
    for ikey, feed in (message.get("feeds") or {}).items():
        self.loop.call_soon_threadsafe(self.tick_q.put_nowait, parse_tick(ikey, feed))

# rolling_state_engine.py — a single consumer owns all mutation
async def ingest_loop(self):
    while True:
        tick = await self.tick_q.get()
        self.process_tick(**tick)          # now genuinely single-writer
```

This makes the README's "single-writer" claim true rather than aspirational, eliminates the thread/asyncio
race on `phantom_candles` and `ltf_df`, gives you natural backpressure and a measurable queue depth (a real
health signal), and makes the ingest path testable by feeding it recorded ticks. It is perhaps 60 lines of
change and it retires an entire category of defect.

---

## §H — Things I checked that turned out fine

Recorded so the assessment is honest in both directions — these were plausible failure modes that the
evidence did **not** support:

1. **The 52-week baselines are real.** I expected `EOD_IV`/`EOD_PCR` to be mostly zeros (because
   [parquet_engine.py:124-126](trading_copilot/data_services/parquet_engine.py#L124-L126) writes `0.0` for
   all but the latest row). Checking the actual parquets: `RELIANCE_1D` has 265 contiguous non-zero
   `EOD_IV` rows and `df.tail(252)` is **252/252 populated**. `master_bootstrap.py` genuinely did its job.
   The `iv_percentile_52w` and `pcr_percentile_52w` figures are computed on real data.
2. **`macro_bootstrap`'s `ffill`/`bfill` is not the disaster it looks like.** It fills the ~758 pre-backfill
   rows, which superficially fabricates history — but the 52-week window is `tail(252)`, which is fully
   populated for the well-covered symbols, so the headline statistics are unaffected. It remains a genuine
   risk for sparsely-covered symbols (`SAIL` has 4 internal gaps; `DIACABS_1D` has **1 row total** and will
   produce meaningless baselines).
3. **`MicrostructureEngine` handles VTT correctly.** The differencing at
   [:142-143](trading_copilot/microstructure_engine.py#L142-L143) is right, including a sensible reconnect
   anomaly clamp at [:146-149](trading_copilot/microstructure_engine.py#L146-L149). The bug in §A-2 is in
   `RollingStateEngine`, not here.
4. **The debounce guard is real and correct** (§B-9) — a genuine 3-consecutive-tick, ~30-second gate.
5. **The LLM JSON repair chain is well-built** — three tiers including a targeted fix for unescaped quotes
   inside `institutional_rationale`
   ([reasoning_engine.py:306-330](trading_copilot/reasoning_engine.py#L306-L330)).
6. **Max Pain is computed correctly** in both live and bootstrap paths
   ([master_bootstrap.py:274-285](trading_copilot/scripts/master_bootstrap.py#L274-L285),
   [parquet_engine.py:68-85](trading_copilot/data_services/parquet_engine.py#L68-L85)) — proper OI-weighted
   intrinsic-loss minimisation, not the shortcut it is often reduced to.
7. **`calc_volume_profile_high_fidelity` implements the Value Area correctly** — contiguous outward
   expansion from POC. It is `macro_bootstrap` that gets it wrong (§C-2), and the correct implementation is
   already available to fix it.
8. **The `playbook_state.json` path works under the documented launch method.** I initially read it as
   unconditionally broken; it is CWD-relative and correct when launched from the repo root as the README
   instructs — it only fails under `start_all.bat`.
9. **Session-phase time parsing works.** `strptime("%I:%M %p")` on the lowercase `"10:30 am"` produced by
   [rolling_state_engine.py:308](trading_copilot/rolling_state_engine.py#L308) is fine — CPython's `%p`
   matching is case-insensitive.
10. **The macro news loop de-duplicates before calling Gemini**
    ([news_engine.py:342-345](trading_copilot/news_engine.py#L342-L345)) — a real and well-judged token
    optimisation.
