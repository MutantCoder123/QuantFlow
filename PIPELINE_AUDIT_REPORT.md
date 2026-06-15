# AlgoTrade Pipeline Audit Report
# Date: 2026-06-13
# Auditor Role: Elite System Architect & Quantitative Trader

================================================================================
                         ARCHITECTURE OVERVIEW
================================================================================

The system operates on a DUAL-STREAM architecture:

  STREAM A (Dashboard):
    WebSocket Tick → MicrostructureEngine → RollingStateEngine.process_tick()
    → MathEngine.generate_signal_payload() → TerminalDashboard.active_states
    → WebSocket broadcast to browser UI
    
    Keys are FLAT: { "vol_z_score_5m": 1.8, "whale_cvd_ema_1h": 450, "ltp": 182.5, ... }

  STREAM B (LLM):
    TerminalDashboard.active_states → ReasoningEngine.build_structured_payload()
    → NESTED JSON payload → LLM prompt → JSON action output
    
    Keys are NESTED: { "1_live_microstructure": { "order_flow": { "vol_z_score_5m": 1.8 } } }

  GATEKEEPER sits BETWEEN streams:
    TerminalDashboard.active_states (Stream A) → IntradayGatekeeper.evaluate()
    → If authorized → build_structured_payload() → LLM (Stream B)

This dual-stream design is the root context for understanding the flaws below.

================================================================================
                     FLAW #1 — CVD VTT RECONNECTION CORRUPTION
================================================================================

SEVERITY:   CRITICAL
LAYER:      Microstructure Engine (Stream A)
FILE:       trading_copilot/microstructure_engine.py, lines 135-140
AFFECTS:    CVD, Whale CVD, Flow Regime, Kinetic Divergence, Gatekeeper

CODE TRACE:
    prev_vol = cls.last_vtt_state.get(token, vol)
    tick_vol = max(0, vol - prev_vol)
    cls.last_vtt_state[token] = vol

PROBLEM:
The Upstox WebSocket reports volume as VTT (Volume Traded Today) — a cumulative
counter that increases monotonically from 0 at market open. The engine computes
per-tick volume by differencing consecutive VTT values.

When the WebSocket disconnects and reconnects mid-session (which happens
routinely due to ISP hiccups, Upstox maintenance, or network congestion):

  1. The reconnection tick carries the current cumulative VTT (e.g., 5,000,000)
  2. The last stored VTT may be from minutes ago (e.g., 4,500,000)
  3. The differencing yields tick_vol = 500,000 — a PHANTOM mega-spike

This phantom volume is injected into:
  - CVD → permanently drifts the directional accumulation signal
  - Whale CVD → virtually guaranteed to exceed the ₹5L threshold
  - Volume Profile POC → distorts the Point of Control price level
  - Session VWAP → corrupts price×volume weighted average

The max(0, ...) clamp silently absorbs negative deltas from VTT resets,
making the corruption invisible in logs.

MARKET CONSEQUENCE:
After a single reconnect, CVD permanently drifts in one direction for the
rest of the trading day. The Gatekeeper's polarity check (whale_cvd_ema_1h
sign) may authorize a trade based on phantom institutional flow that does
not exist. The LLM receives corrupted flow data and may issue a high-
confidence Long recommendation when actual institutional flow is neutral.

SOLUTION:
    # Guard against VTT anomalies:
    tick_vol = max(0, vol - prev_vol)
    
    # If tick_vol exceeds 50% of previously accumulated VTT, it's likely a
    # reconnection spike — not real tick-level volume
    if prev_vol > 0 and tick_vol > (prev_vol * 0.5):
        logger.warning(f"VTT anomaly for {token}: tick_vol={tick_vol}. Clamping.")
        tick_vol = 0
    
    # Additionally, implement a daily state reset at 09:15 IST:
    # Clear cvd_state, whale_cvd_state, session_vwap_state, vol_profile_state
    # on date boundary to prevent overnight state corruption.


================================================================================
                FLAW #2 — STATIC ₹5L WHALE THRESHOLD
================================================================================

SEVERITY:   MEDIUM
LAYER:      Microstructure Engine (Stream A)
FILE:       trading_copilot/microstructure_engine.py, line 87
AFFECTS:    Whale CVD, Gatekeeper polarity checks

CODE TRACE:
    transaction_value = float(ltp * volume)
    if transaction_value > 500000:
        # ... update whale CVD

PROBLEM:
The ₹500,000 (₹5 Lakh) threshold is applied uniformly across all stocks.

  RELIANCE (LTP ≈ ₹1,300):  ₹5L = ~385 shares. Every mid-size retail order
  clears this bar. The "whale" filter captures RETAIL noise.

  IDEA (LTP ≈ ₹15):  ₹5L = ~33,333 shares. Only genuine block deals clear
  this bar. The filter is too aggressive — it misses genuine mid-tier
  institutional activity (e.g., 10,000 share accumulation = ₹1.5L).

MARKET CONSEQUENCE:
For high-priced stocks, Whale CVD ≈ regular CVD (no information gain).
For low-priced stocks, Whale CVD reads near-zero for hours, starving the
Gatekeeper's directional classifier of its most important input signal.

SOLUTION:
    # Option A: Scale to stock's LTP
    whale_threshold = max(500000, ltp * 5000)
    
    # Option B: Load 20-day Average Daily Turnover from parquet
    # Set threshold = ADT × 0.001 (0.1% of daily turnover)
    adt = daily_metrics_cache.get(symbol, {}).get('avg_daily_turnover', 50000000)
    whale_threshold = adt * 0.001


================================================================================
               FLAW #3 — VWAP RESET OFF-BY-ONE BUG
================================================================================

SEVERITY:   MEDIUM
LAYER:      Microstructure Engine (Stream A)
FILE:       trading_copilot/microstructure_engine.py, line 69
AFFECTS:    Session VWAP, price_to_vwap_pct, Gatekeeper VETO

CODE TRACE:
    if state['last_reset_date'] != current_date_str and now.hour >= 9 and now.minute >= 15:
        state['cumulative_pv'] = 0.0
        state['cumulative_v'] = 0.0
        state['last_reset_date'] = current_date_str

PROBLEM:
The reset requires BOTH date change AND time >= 09:15. If the first tick of
the new session arrives during the pre-open auction (09:00-09:14), the VWAP
does NOT reset. Yesterday's cumulative price×volume carries forward, polluting
the entire day's VWAP calculation.

The pre-open auction is precisely when institutional orders concentrate
(the call auction mechanism). Missing these ticks or corrupting their VWAP
baseline is a significant data quality issue.

MARKET CONSEQUENCE:
price_to_vwap_pct — the single most important Gatekeeper input (used in the
VETO override at line 141, Gate 2 at line 150) — starts the day corrupted.
The Gatekeeper's stealth accumulation detector may fire or fail to fire on
stale VWAP data.

SOLUTION:
    # Reset on date boundary alone, not time:
    if state['last_reset_date'] != current_date_str:
        state['cumulative_pv'] = 0.0
        state['cumulative_v'] = 0.0
        state['last_reset_date'] = current_date_str
        # Also reset CVD and Whale CVD for a clean session:
        cls.cvd_state.pop(token, None)
        cls.whale_cvd_state.pop(token, None)


================================================================================
          FLAW #4 — bfill() FABRICATES INDICATOR VALUES AFTER COLD START
================================================================================

SEVERITY:   CRITICAL
LAYER:      Technical Engine (Stream A)
FILE:       trading_copilot/technical_engine.py, lines 41-43
AFFECTS:    RSI, MACD, Bollinger Bands, all downstream signals

CODE TRACE:
    df['rsi_14'] = ta.rsi(df['close'], length=14)
    # ... MACD, BB calculations ...
    df.bfill(inplace=True)   # <-- THE PROBLEM
    df.dropna(inplace=True)

PROBLEM:
Technical indicators require a minimum number of historical bars to produce
valid values:
  - RSI-14 needs 14 bars → first 13 rows are NaN
  - MACD(12,26,9) needs 35 bars → first 34 rows are NaN
  - Bollinger Bands(20) needs 20 bars → first 19 rows are NaN

bfill() (backward fill) replaces these NaN values with the FIRST VALID value
calculated. This means:
  - Row 1 through Row 13 all show the SAME RSI value as Row 14
  - Row 1 through Row 34 all show the SAME MACD as Row 35
  - This creates phantom "flat zones" that can be misinterpreted as:
    * A "MACD crossover" when MACD transitions from fabricated-flat to real
    * A "RSI divergence" between fabricated RSI and real price movement

This function is called inside calc_trend_and_momentum() AND
calc_institutional_volume(), meaning the fabrication is applied TWICE.

MARKET CONSEQUENCE:
After every cold start, watchlist addition, or WebSocket reconnect (which
triggers a historical data re-fetch), the first 35+ candles produce fabricated
indicators. If the Gatekeeper triggers during this window, the LLM receives
a payload where MACD "just crossed bullish" — but the crossover is an artifact
of backfilling, not a real market signal.

SOLUTION:
    # Option A: Never backfill oscillators. Let NaN propagate naturally.
    # In generate_signal_payload(), add maturity tracking:
    min_required = 35  # MACD needs 35 bars
    data_maturity = min(1.0, len(df) / min_required)
    payload["data_maturity"] = round(data_maturity, 2)
    
    # Option B: Use fillna(50) for RSI (neutral) and fillna(0) for MACD
    # (no signal) instead of bfill:
    df['rsi_14'] = ta.rsi(df['close'], length=14).fillna(50.0)
    df['macd_hist'] = macd['MACDh_12_26_9'].fillna(0.0)


================================================================================
          FLAW #5 — OMNI RESAMPLER RUNS EVERY 1.5 SECONDS (WASTE)
================================================================================

SEVERITY:   MEDIUM (Performance)
LAYER:      Technical Engine (Stream A)
FILE:       trading_copilot/technical_engine.py, lines 257-285
            trading_copilot/rolling_state_engine.py, line 369-370
AFFECTS:    CPU usage, loop latency

CODE TRACE:
    # In calculate_technicals_loop() — runs every 1.5 seconds:
    omni_dfs = MathEngine.generate_omni_dataframes(df_copy)  # Resamples to 15m,30m,1h,4h
    omni_metrics = MathEngine.calc_omni_metrics(omni_dfs)     # RSI/MACD/EMA/ATR/BB × 5 TFs

PROBLEM:
generate_omni_dataframes() resamples the ENTIRE 5-minute DataFrame into 4
higher timeframes every 1.5 seconds, for every symbol. calc_omni_metrics()
then computes 8 indicators on each of those 5 DataFrames.

With 25 watchlist symbols:
  25 symbols × 5 timeframes × 8 indicators = 1,000 indicator calculations
  every 1.5 seconds.

The 4H timeframe physically cannot change between two 1.5-second ticks. It
changes once every 4 hours. The 1H changes once per hour. Even the 15M only
changes once every 15 minutes.

MARKET CONSEQUENCE:
No direct trading impact, but the excessive computation increases loop latency.
If the loop takes > 1.5 seconds to process all 25 symbols, it starts falling
behind real-time — ticks accumulate in the phantom candle but technicals lag.

SOLUTION:
    # Cache omni results per timeframe with boundary-based invalidation:
    omni_cache = {}  # {token: {tf: {last_boundary: timestamp, metrics: dict}}}
    
    for tf, freq in [('15m','15min'), ('30m','30min'), ('1h','1h'), ('4h','4h')]:
        current_boundary = tick_ts.floor(freq)
        cached = omni_cache.get(token, {}).get(tf, {})
        if cached.get('last_boundary') == current_boundary:
            continue  # Nothing changed — use cached metrics
        # Else: recompute only this timeframe


================================================================================
     FLAW #6 — KINETIC DIVERGENCE NAME COLLISION (SIGNAL OVERWRITE)
================================================================================

SEVERITY:   CRITICAL
LAYER:      MTF Extractor → Reasoning Engine (Stream A → Stream B junction)
FILES:      trading_copilot/mtf_extractor.py, lines 82-95
            trading_copilot/reasoning_engine.py, lines 159-168, 193-198
AFFECTS:    LLM receives wrong divergence signal

CODE TRACE:
    # mtf_extractor.py — extract_all() returns:
    {
        "kinetic_divergence": cls.calc_kinetic_divergence(payload, ltp),
        # Returns: "BEARISH_KINETIC_DIVERGENCE" / "BULLISH_KINETIC_DIVERGENCE" / "MOMENTUM_CONFIRMED"
    }
    
    # reasoning_engine.py — build_structured_payload() line 194-198:
    "mtf_technicals": {
        **MTFFeatureExtractor.extract_all(payload, ltp),   # Includes "kinetic_divergence": "BEARISH_..."
        "kinetic_divergence": {                             # OVERWRITES with a dict!
            "whale_cvd_slope": whale_cvd_slope,
            "divergence_state": divergence_state            # "HIDDEN_BULLISH_ABSORPTION" etc.
        }
    }

PROBLEM:
Python's {**dict_a, "key": new_value} syntax means the LATER key wins.
MTFFeatureExtractor.extract_all() produces:
    "kinetic_divergence": "BEARISH_KINETIC_DIVERGENCE"

Then the reasoning engine immediately overwrites it with:
    "kinetic_divergence": {"whale_cvd_slope": ..., "divergence_state": ...}

The MTF extractor's EMA-MACD divergence signal — which detects momentum
exhaustion BEFORE price confirms it — is silently destroyed. The LLM never
sees it.

These are TWO COMPLETELY DIFFERENT signals:
  - MTF's: Price vs EMA vs MACD histogram alignment (technical momentum)
  - Engine's: Price-to-VWAP vs Whale CVD slope (institutional flow divergence)

Both are valuable. Losing either one degrades the LLM's decision quality.

MARKET CONSEQUENCE:
The LLM prompt says "Evaluate kinetic_divergence.divergence_state" — it reads
the whale-flow version. But the EMA-MACD divergence (which can detect a
bearish reversal 2-3 candles before whale CVD confirms it) is gone. The LLM
may hold a Long position through a momentum exhaustion signal it never received.

SOLUTION:
    "mtf_technicals": {
        **MTFFeatureExtractor.extract_all(payload, ltp),
        # Rename to avoid collision:
        "vwap_whale_divergence": {
            "whale_cvd_slope": whale_cvd_slope,
            "divergence_state": divergence_state
        }
    }
    # The MTF's original "kinetic_divergence" key survives untouched.
    # Update the LLM prompt to reference BOTH divergence types.


================================================================================
        FLAW #7 — BOLLINGER SQUEEZE MISLABELED AS "EXPANSION"
================================================================================

SEVERITY:   MEDIUM
LAYER:      MTF Extractor (Stream A → Stream B)
FILE:       trading_copilot/mtf_extractor.py, lines 58-62
AFFECTS:    LLM interpretation of volatility regime

CODE TRACE:
    dist = bbu - bbl
    if dist < (atr * 1.5):
        if tf in ['15m', '30m']:
            return "15M_30M_COILING_SQUEEZE"
        else:
            return "1H_VOLATILITY_EXPANSION"  # <-- WRONG LABEL

PROBLEM:
When Bollinger Band width (high - low) is LESS than 1.5× ATR, it means
volatility is contracting — a classic Bollinger Squeeze setup. This is
correctly labeled as "COILING_SQUEEZE" for the 15m/30m timeframes.

But for the 1H timeframe, the exact same condition (tight bands = low
volatility) is labeled "1H_VOLATILITY_EXPANSION" — the semantic opposite.

MARKET CONSEQUENCE:
The LLM reads "VOLATILITY_EXPANSION" on the 1H and may:
  - Avoid entering new positions (expansion = expensive premium = risky entries)
  - Expect wide price swings when the opposite is happening
A squeeze on the 1H is actually the highest-conviction breakout setup. The
inverted label may cause the LLM to AVOID the exact moment it should be
entering with maximum conviction.

SOLUTION:
    if dist < (atr * 1.5):
        if tf in ['15m', '30m']:
            return "15M_30M_COILING_SQUEEZE"
        else:
            return "1H_COILING_SQUEEZE"   # Fixed label
    
    # Add actual expansion detection:
    if dist > (atr * 3.0):
        return f"{tf.upper()}_VOLATILITY_EXPANSION"


================================================================================
         FLAW #8 — 15:15 IST FORCE-CLOSE HOUR/MINUTE LOGIC BUG
================================================================================

SEVERITY:   CRITICAL
LAYER:      Intraday Gatekeeper
FILE:       trading_copilot/intraday_gatekeeper.py, line 47
AFFECTS:    Position safety net, auto-square-off

CODE TRACE:
    if current_time_ist.hour >= 15 and current_time_ist.minute >= 15:
        return cls._create_response("Close", priority=10, confidence=10, llm_auth=False)

PROBLEM:
The AND conjunction requires BOTH conditions simultaneously:
    
    Time       hour>=15  min>=15   Result
    ─────────  ────────  ───────   ──────
    14:59      False     True      NO CLOSE
    15:00      True      False     NO CLOSE  ← BUG
    15:10      True      False     NO CLOSE  ← BUG
    15:14      True      False     NO CLOSE  ← BUG
    15:15      True      True      CLOSE ✓
    15:30      True      True      CLOSE ✓
    16:00      True      False     NO CLOSE  ← BUG
    16:10      True      False     NO CLOSE  ← BUG
    16:14      True      False     NO CLOSE  ← BUG
    16:15      True      True      CLOSE ✓

The system fails to force-close between 15:00-15:14 and 16:00-16:14.

MARKET CONSEQUENCE:
Indian brokerages auto-square-off MIS (intraday) positions between 15:15-15:20.
If the Gatekeeper is supposed to signal an exit BEFORE the brokerage force-
closes (to get a better fill price), it fails during the critical 15:00-15:14
window. Additionally, any post-market cleanup between 16:00-16:14 is silently
skipped.

SOLUTION:
    # Use decimal time comparison:
    current_decimal = current_time_ist.hour + current_time_ist.minute / 60.0
    if current_decimal >= 15.25:  # 15:15 IST = 15 + 15/60 = 15.25
        return cls._create_response("Close", priority=10, confidence=10, llm_auth=False)


================================================================================
          FLAW #9 — GATE 2 HAS A VACUOUS abs() > 0 CONDITION
================================================================================

SEVERITY:   MEDIUM
LAYER:      Intraday Gatekeeper
FILE:       trading_copilot/intraday_gatekeeper.py, line 150
AFFECTS:    Gate 2 entry authorization

CODE TRACE:
    gate_2_active = abs(price_to_vwap_pct) <= 0.2 and vol_z_score_5m >= 1.5 and abs(whale_cvd_ema_1h) > 0

PROBLEM:
abs(whale_cvd_ema_1h) > 0 is true for ANY non-zero floating point value,
including 0.0001. Once the Whale CVD receives a single tick, this condition
becomes permanently true for the rest of the session.

The intent was to verify meaningful institutional directional flow. The
implementation verifies nothing — it's a tautology.

MARKET CONSEQUENCE:
Gate 2 effectively reduces to just:
    abs(price_to_vwap_pct) <= 0.2 and vol_z_score_5m >= 1.5

The whale CVD directional conviction check, which should confirm that
institutions are actively positioning (not just a few retail trades), provides
zero filtering.

SOLUTION:
    # Require meaningful institutional flow volume:
    gate_2_active = (abs(price_to_vwap_pct) <= 0.2 and 
                     vol_z_score_5m >= 1.5 and 
                     abs(whale_cvd_ema_1h) > 500)  # At least 500 shares of whale flow
    
    # Better: normalize to ADT or use a percentile threshold


================================================================================
    *** FLAW #10 — GATEKEEPER READS WRONG DATA STREAM (ROOT CAUSE) ***
================================================================================

SEVERITY:   CRITICAL (HIGHEST PRIORITY)
LAYER:      Gatekeeper ↔ Stream A junction
FILES:      trading_copilot/intraday_gatekeeper.py, lines 60-78
            trading_copilot/reasoning_engine.py, lines 401-409
AFFECTS:    ALL GATEKEEPER GATES — entire entry authorization pipeline

CODE TRACE (Caller — reasoning_engine.py line 401-409):
    for symbol, payload in list(TerminalDashboard.active_states.items()):
        # `payload` is the FLAT dashboard dict (Stream A)
        gatekeeper_res = IntradayGatekeeper.evaluate(payload, {...}, ltp)

CODE TRACE (Gatekeeper — intraday_gatekeeper.py line 62-66):
    struct = payload.get("structured_payload", payload)     # Falls back to `payload` (flat)
    micro = struct.get("1_live_microstructure", {})          # Returns {} (key doesn't exist)
                  .get("order_flow", {})                     # Returns {}
    vol_z_score_5m = float(micro.get("vol_z_score_5m", 0.0))   # Always 0.0
    whale_cvd_ema_1h = float(micro.get("whale_cvd_ema_1h", 0.0)) # Always 0.0
    flow_regime = micro.get("flow_regime", "")                    # Always ""

PROBLEM:
The Gatekeeper is called with TerminalDashboard.active_states (Stream A),
which is a FLAT dictionary:
    {
        "vol_z_score_5m": 1.8,
        "whale_cvd_ema_1h": 450.0,
        "price_to_vwap_pct": -0.03,
        "ltp": 182.5,
        ...
    }

But the Gatekeeper navigates a NESTED path designed for Stream B:
    payload["1_live_microstructure"]["order_flow"]["vol_z_score_5m"]

This nested path does NOT EXIST in the flat dict. The fallback chain:
    struct = payload (flat dict)
    micro = flat_dict.get("1_live_microstructure", {}) → {}
    vol_z_score_5m = {}.get("vol_z_score_5m", 0.0) → 0.0

Result: EVERY metric the Gatekeeper reads is permanently 0.0 or "".

FIELD-BY-FIELD ANALYSIS:
    vol_z_score_5m    → 0.0 (exists in flat dict, but read from wrong path)
    whale_cvd_ema_1h  → 0.0 (exists in flat dict, but read from wrong path)
    flow_regime       → ""  (NEVER exists in flat dict — only computed in Stream B)
    distance_to_poc_pct → 0.0 (read from nested "3_macro_..." path — doesn't exist)
    kinetic_divergence → ""  (read from nested "1_live_micro..." path — doesn't exist)
    price_to_vwap_pct → WORKS (line 78 has a fallback: payload.get("price_to_vwap_pct"))

GATE STATUS WITH ZEROED DATA:
    VETO Override: flow_regime == "ABSORPTION_BUYING" → "" == "ABSORPTION_BUYING" → FALSE (DEAD)
    Gate 1: vol_z_score_5m >= 2.0 → 0.0 >= 2.0 → FALSE (DEAD)
    Gate 2: vol_z_score_5m >= 1.5 → 0.0 >= 1.5 → FALSE (DEAD)
    Gate 3: vol_z_score_5m >= 1.5 → 0.0 >= 1.5 → FALSE (DEAD)
    
    ALL GATES ARE PERMANENTLY CLOSED. No stock can ever pass.

MARKET CONSEQUENCE:
This is the ROOT CAUSE of "no stocks are passing the Gatekeeper." The entire
entry authorization system is a dead letter. Every stock receives Action: "Wait"
with llm_authorized: False. The LLM is never triggered by the automatic loop.
Only manual "Instant Analyze" button clicks bypass the Gatekeeper.

SOLUTION:
    # Option A: Read directly from flat payload keys (simple fix):
    vol_z_score_5m = float(payload.get("vol_z_score_5m", 0.0))
    whale_cvd_ema_1h = float(payload.get("whale_cvd_ema_1h", 0.0))
    price_to_vwap_pct = float(payload.get("price_to_vwap_pct", 100.0))
    
    # For flow_regime: compute it inside the Gatekeeper or add it to Stream A
    cvd = float(payload.get("cvd", 0.0))
    obi = float(payload.get("obi", 0.0))
    if cvd > 0 and obi > 0.05: flow_regime = "AGGRESSIVE_BUYING"
    elif cvd < 0 and obi < -0.05: flow_regime = "AGGRESSIVE_SELLING"
    elif cvd > 0 and obi < -0.05: flow_regime = "ABSORPTION_SELLING"
    elif cvd < 0 and obi > 0.05: flow_regime = "ABSORPTION_BUYING"
    else: flow_regime = "NEUTRAL_FLOW"
    
    # For kinetic_divergence: read from flat payload
    # (Requires adding it to rolling_state_engine's final_payload)
    
    # Option B: Call build_structured_payload() first, then pass to Gatekeeper
    # (More expensive but ensures schema alignment)


================================================================================
         FLAW #11 — DUPLICATE analyze_stock METHOD DEFINITION
================================================================================

SEVERITY:   MEDIUM (Code Quality)
LAYER:      Reasoning Engine (Stream B)
FILE:       trading_copilot/reasoning_engine.py, lines 40-52 AND lines 256-389
AFFECTS:    Code maintainability

PROBLEM:
There are two @classmethod definitions of analyze_stock() in the same class.
Python silently overwrites the first with the second. The first definition
(lines 40-52) is dead code — it fetches the payload, imports modules, but
has no return statement and no processing logic.

SOLUTION:
    # Delete lines 39-56 entirely (the first dead definition)


================================================================================
          FLAW #12 — NO FEW-SHOT EXAMPLE IN LLM PROMPT
================================================================================

SEVERITY:   MEDIUM
LAYER:      Reasoning Engine (Stream B)
FILE:       trading_copilot/reasoning_engine.py, lines 291-326
AFFECTS:    LLM output reliability, JSON parse success rate

PROBLEM:
The prompt schema uses invalid JSON placeholders:
    "Entry_Target_Price": <float or null>,
    
These are not valid JSON and may confuse the model. Additionally, there is
no concrete example output. LLMs produce dramatically more reliable structured
output when given at least one few-shot example.

The evidence that this is a real problem: lines 344-350 strip markdown code
blocks from the output, indicating the LLM frequently wraps its JSON in
```json blocks despite being told not to.

SOLUTION:
    # Add before the schema:
    """
    EXAMPLE (reference only):
    {"Action":"Long","Entry_Target_Price":182.5,"Stoploss":179.0,
     "Exit_Target_Price":189.0,"Confidence_Score":8,"Risk_Percentage":1.9,
     "Priority_Score":8,"Reason":"Hidden Bullish Absorption confirmed by
     whale CVD slope +2.4k at VWAP. 5Y POC floor at 178 provides stop."}
    """
    
    # Better: Use Gemini's structured output / response_schema if available
    # to guarantee valid JSON at the API level.


================================================================================
       FLAW #13 — FLOW REGIME NOT REFERENCED IN LLM PROMPT
================================================================================

SEVERITY:   MEDIUM
LAYER:      Reasoning Engine (Stream B)
FILE:       trading_copilot/reasoning_engine.py, lines 297-310
AFFECTS:    LLM utilization of flow regime signal

PROBLEM:
flow_regime is computed (line 100-112), embedded in the payload under
1_live_microstructure.order_flow.flow_regime, but NEVER mentioned by name
in the 4-Block Synthesis Matrix prompt. The LLM must independently discover
and correctly interpret this field.

The flow regime is arguably the MOST powerful pre-computed signal in the
payload. "ABSORPTION_BUYING" means institutions are buying while retail
sells — the quintessential stealth accumulation pattern that precedes
sharp breakouts. If the LLM ignores it, you lose a critical edge.

SOLUTION:
    # Add to BLOCK 1 instructions:
    """
    - Evaluate `order_flow.flow_regime`:
      - 'ABSORPTION_BUYING': Institutions accumulating against retail selling.
        Heavily favor LONG. This is the highest-conviction stealth entry signal.
      - 'ABSORPTION_SELLING': Institutions distributing against retail buying.
        Heavily favor SHORT or CLOSE existing longs.
      - 'AGGRESSIVE_BUYING/SELLING': Momentum-confirmed directional move.
      - 'NEUTRAL_FLOW': No institutional signal. Require extra confluence.
    """


================================================================================
          FLAW #14 — PAYLOAD TOKEN OVERFLOW / ATTENTION DILUTION
================================================================================

SEVERITY:   MEDIUM
LAYER:      Reasoning Engine (Stream B)
FILE:       trading_copilot/reasoning_engine.py, line 288
AFFECTS:    LLM cost, output quality

PROBLEM:
The payload is serialized with json.dumps(payload_copy, indent=2). It contains:
    - ~40 omni-metrics (5 timeframes × 8 indicators per TF)
    - Full geometry dict (double_bottom, double_top, head_and_shoulders + levels)
    - Full candlestick patterns (6 patterns)
    - Camarilla pivots (H4, H3, L3, L4)
    - Raw news array (unbounded length)
    - Global market context (unbounded)
    - User position context

With indent=2, the payload easily exceeds 3,000-4,000 tokens. Combined with
the ~800 token system prompt, each call burns 4,000+ input tokens.

More critically: LLMs exhibit attention degradation on long contexts. The
critical signal (whale_cvd_slope, flow_regime) may be buried under 40
omni-metric fields that the prompt NEVER references by name.

SOLUTION:
    # 1. Remove indentation — saves ~40% token count:
    user_payload = json.dumps(payload_copy, separators=(',', ':'))
    
    # 2. Prune omni-metrics to only referenced timeframes (15m, 1h, 1d):
    KEEP_TIMEFRAMES = ['15m', '1h', '1d']
    # Remove 5m, 30m, 4h from payload
    
    # 3. Truncate raw_news to 3 most recent items, 100 chars each
    
    # 4. Add a compact summary field the LLM can scan first:
    payload["_quick_read"] = (
        f"LTP={ltp} VWAP%={price_to_vwap_pct:.2f} VolZ={vol_z_score_5m:.1f} "
        f"REGIME={flow_regime} WHALE_CVD={whale_cvd_ema_1h:.0f} "
        f"DIVERGENCE={divergence_state}"
    )


================================================================================
     FLAW #15 — CLASS-LEVEL MUTABLE STATE RACE CONDITIONS
================================================================================

SEVERITY:   MEDIUM
LAYER:      Architecture (Cross-cutting)
FILES:      trading_copilot/microstructure_engine.py (cvd_state, vol_profile_state)
            trading_copilot/rolling_state_engine.py (live_options_state)
            trading_copilot/reasoning_engine.py (latest_reports, active_loops)
            trading_copilot/diagnostic_ui.py (active_states)
AFFECTS:    Data integrity under concurrent access

PROBLEM:
Nearly every engine uses class-level mutable dictionaries as shared state:
    cvd_state = {}
    active_states = {}
    latest_reports = {}
    whale_cvd_state = {}

The WebSocket callback thread writes to MicrostructureEngine.cvd_state,
while the asyncio event loop reads from it in calculate_technicals_loop().
There are no locks, no thread-safe containers, and no copy-on-read semantics.

CPython's GIL protects simple dict get/set operations, but COMPOUND operations
like:
    cls.cvd_state[token] += int(volume)

are NOT atomic. This is a read-modify-write sequence:
    1. READ cls.cvd_state[token]  → value = 1000
    2. Another thread writes cls.cvd_state[token] = 500
    3. WRITE cls.cvd_state[token] = 1000 + volume  → overwrites the 500

MARKET CONSEQUENCE:
Under heavy tick load (market open 09:15, expiry day 15:00-15:30), you may
see intermittent CVD jumps, phantom OBI spikes, or silently dropped updates.
These are non-reproducible bugs — the worst kind to debug in production.

SOLUTION:
    import threading
    
    class MicrostructureEngine:
        _lock = threading.Lock()
        cvd_state = {}
        
        @classmethod
        def update_cvd(cls, token, ltp, volume, best_bid, best_ask):
            with cls._lock:
                if token not in cls.cvd_state:
                    cls.cvd_state[token] = 0
                if ltp >= best_ask:
                    cls.cvd_state[token] += int(volume)
                elif ltp <= best_bid:
                    cls.cvd_state[token] -= int(volume)
                return cls.cvd_state[token]


================================================================================
                         PRIORITY FIX ORDER
================================================================================

    PRIORITY 1:  Flaw #10 — Gatekeeper reads wrong stream (ALL gates dead)
    PRIORITY 2:  Flaw #8  — 15:15 force-close logic bug
    PRIORITY 3:  Flaw #1  — CVD reconnection corruption
    PRIORITY 4:  Flaw #4  — bfill() fabricates indicators
    PRIORITY 5:  Flaw #6  — Kinetic divergence name collision

    Flaws #10 and #8 can be fixed in under 30 minutes each.
    Flaws #1 and #4 require 1-2 hours each with testing.
    Flaw #6 requires a prompt update alongside the code fix.

================================================================================
                           END OF REPORT
================================================================================
