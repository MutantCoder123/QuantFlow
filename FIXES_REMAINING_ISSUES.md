# Remaining Fix Issues — Post-Audit Refinement Report
# Date: 2026-06-13
# Reviewer Role: Elite Quantitative Trader

================================================================================
                         EXECUTIVE SUMMARY
================================================================================

Out of 12 fixes applied from the Pipeline Audit, 3 have edge-case issues
that need refinement before the next live trading session. None are
crash-level bugs — the system will run — but each can produce incorrect
signal classifications in specific market conditions.

================================================================================
          ISSUE 1 — VTT ANOMALY GUARD OVER-CLAMPS AT MARKET OPEN
================================================================================

FILE:       trading_copilot/microstructure_engine.py, lines 145-149
ORIGINAL FLAW: #1 (CVD Reconnection Corruption)
STATUS:     Fix is DIRECTIONALLY CORRECT but has an early-session edge case

CURRENT CODE:
    prev_vol = cls.last_vtt_state.get(token, vol)
    tick_vol = max(0, vol - prev_vol)

    # Guard against VTT anomalies (e.g. WebSocket reconnections)
    if prev_vol > 0 and tick_vol > (prev_vol * 0.5):
        logger.warning(...)
        tick_vol = 0

THE PROBLEM:
The guard compares tick_vol (the delta) against prev_vol (the CUMULATIVE VTT).
During the trading session, prev_vol is large (e.g. 5,000,000), so the
threshold (2,500,000) is safely unreachable by any legitimate tick. This is
correct behavior.

However, at market open (09:15 IST), cumulative VTT starts from 0:
    Tick 1: vol = 500    → prev_vol = 500,   tick_vol = 0 (initial)
    Tick 2: vol = 1200   → prev_vol = 500,   tick_vol = 700
                           Guard: 700 > (500 * 0.5) = 250 → TRUE → CLAMPED!
    Tick 3: vol = 2000   → prev_vol = 1200,  tick_vol = 800
                           Guard: 800 > (1200 * 0.5) = 600 → TRUE → CLAMPED!

The first 5-10 ticks of the session will almost certainly be clamped because
early cumulative VTT is small, making the 50% threshold trivially easy to
exceed with normal volume deltas.

MARKET CONSEQUENCE:
CVD, Whale CVD, VWAP, and Volume Profile receive ZERO volume for the first
2-3 minutes of the session. The opening auction — which is the single most
information-rich moment of the Indian trading day — is silently dropped.
The Gatekeeper's vol_z_score_5m will read near-zero, preventing any
early-session Gate triggers.

RECOMMENDED FIX:
    # Add a minimum cumulative volume floor before the anomaly guard activates
    if prev_vol > 100000 and tick_vol > (prev_vol * 0.5):
        logger.warning(...)
        tick_vol = 0

    # prev_vol > 100000 ensures the guard only activates after meaningful
    # session volume has accumulated (~100K shares). Before that, all ticks
    # pass through unfiltered, which is correct behavior for market open.


================================================================================
  ISSUE 2 — INCOMPLETE fillna COVERAGE AFTER bfill REMOVAL
================================================================================

FILE:       trading_copilot/technical_engine.py, lines 40-49 and 100-102
ORIGINAL FLAW: #4 (bfill Fabricates Indicators)
STATUS:     Fix is PARTIALLY CORRECT — RSI/MACD/CMF covered, others missed

CURRENT CODE (calc_trend_and_momentum):
    df['rsi_14'] = df['rsi_14'].fillna(50.0)        ✅ Covered
    df['macd'] = df['macd'].fillna(0.0)              ✅ Covered
    df['macd_hist'] = df['macd_hist'].fillna(0.0)    ✅ Covered
    df['macd_signal'] = df['macd_signal'].fillna(0.0) ✅ Covered

    # NOT COVERED:
    df['ema_9']    → NaN for first 8 rows            ❌ Missing
    df['ema_21']   → NaN for first 20 rows           ❌ Missing
    df['bb_lower'] → NaN for first 19 rows           ❌ Missing
    df['bb_mid']   → NaN for first 19 rows           ❌ Missing
    df['bb_upper'] → NaN for first 19 rows           ❌ Missing
    df['atr_14']   → NaN for first 13 rows           ❌ Missing

CURRENT CODE (calc_institutional_volume):
    df['cmf'] = df['cmf'].fillna(0.0)               ✅ Covered

    # NOT COVERED:
    df['vwap']       → potentially NaN               ❌ Missing
    df['vwap_lower'] → potentially NaN               ❌ Missing
    df['vwap_upper'] → potentially NaN               ❌ Missing
    df['vol_z_score'] → first 19 rows NaN (rolling)  ❌ Missing

WHY IT MATTERS:
The old code used bfill() + dropna() which removed ALL NaN rows. The fix
correctly removed bfill() but also removed dropna(). This means NaN values
now propagate downstream.

In generate_signal_payload() (line 378):
    last_row = processed_df.iloc[-1]

The last row is the PHANTOM CANDLE (latest tick), which should always have
valid data. However, the safe_float() function on line 380:
    def safe_float(val):
        return 0.0 if pd.isna(val) else round(float(val), 2)

This converts NaN → 0.0 silently. So it won't crash, but:
    - ema_9 = 0.0 when LTP is ₹180 → MTF extractor calculates
      (180 - 0) = 180 > (2.5 * atr) → "OVERSTRETCHED" classification
    - bb_upper = 0.0, bb_lower = 0.0 → dist = 0 < (atr * 1.5)
      → "COILING_SQUEEZE" when there's actually no squeeze

MARKET CONSEQUENCE:
After a cold start or new symbol addition, for the first ~20 candles
(~100 minutes at 5-min resolution), the MTF Extractor may report false
OVERSTRETCHED or COILING_SQUEEZE states. If the Gatekeeper triggers
during this window, the LLM receives fabricated volatility regimes.

RECOMMENDED FIX:
    # In calc_trend_and_momentum(), after all indicators:
    df['ema_9'] = df['ema_9'].fillna(df['close'])
    df['ema_21'] = df['ema_21'].fillna(df['close'])
    df['bb_lower'] = df['bb_lower'].fillna(df['close'] * 0.98)
    df['bb_mid'] = df['bb_mid'].fillna(df['close'])
    df['bb_upper'] = df['bb_upper'].fillna(df['close'] * 1.02)
    df['atr_14'] = df['atr_14'].fillna(method='ffill').fillna(0.0)

    # In calc_institutional_volume(), after all indicators:
    df['vwap'] = df['vwap'].fillna(df['close'])
    df['vwap_lower'] = df['vwap_lower'].fillna(df['close'] * 0.98)
    df['vwap_upper'] = df['vwap_upper'].fillna(df['close'] * 1.02)
    df['vol_z_score'] = df['vol_z_score'].fillna(0.0)

    # Rationale: 
    # - EMA → use close price (EMA converges to price as period → 0)
    # - Bollinger Bands → use close ± 2% (approximation of 2σ)
    # - ATR → forward-fill then 0 (ATR is a slow-moving metric)
    # - vol_z_score → 0.0 (neutral, no volume anomaly)
    # - VWAP → use close price (VWAP ≈ price when no history)


================================================================================
  ISSUE 3 — GATEKEEPER DIVERGENCE THRESHOLDS DON'T MATCH REASONING ENGINE
================================================================================

FILE:       trading_copilot/intraday_gatekeeper.py, lines 86-92
ORIGINAL FLAW: #10 (Dead Gatekeeper — kinetic_divergence emulation)
STATUS:     Fix is FUNCTIONALLY CORRECT but thresholds are MISALIGNED

CURRENT CODE (Gatekeeper, lines 89-92):
    if price_to_vwap_pct < -0.05 and whale_cvd_slope > 1000:
        kinetic_divergence = "HIDDEN_BULLISH_ABSORPTION"
    elif price_to_vwap_pct > 0.05 and whale_cvd_slope < -1000:
        kinetic_divergence = "HIDDEN_BEARISH_DISTRIBUTION"

REASONING ENGINE CODE (lines 142-148):
    elif price_to_vwap_pct < -0.1 and whale_cvd_slope > 0:
        divergence_state = "HIDDEN_BULLISH_ABSORPTION"
    elif price_to_vwap_pct > 0.1 and whale_cvd_slope < 0:
        divergence_state = "HIDDEN_BEARISH_DISTRIBUTION"

COMPARISON TABLE:
    ┌──────────────────────────┬──────────────────┬──────────────────┐
    │ Parameter                │ Gatekeeper       │ Reasoning Engine │
    ├──────────────────────────┼──────────────────┼──────────────────┤
    │ VWAP deviation threshold │ 0.05%            │ 0.1%             │
    │ Whale CVD slope threshold│ ±1000            │ ±0 (any sign)    │
    └──────────────────────────┴──────────────────┴──────────────────┘

IMPACT ANALYSIS:
The Gatekeeper is STRICTER on slope (requires ±1000) but LOOSER on VWAP
deviation (0.05% vs 0.1%). This creates a DISAGREEMENT ZONE:

  Case A: price_to_vwap_pct = -0.07, whale_cvd_slope = +500
    Gatekeeper: slope 500 < 1000 → NO DIVERGENCE → kinetic_divergence = ""
    ReasoningEngine: slope 500 > 0 → "HIDDEN_BULLISH_ABSORPTION"
    Result: Gatekeeper's Gate 1 does NOT trigger, but if the LLM had been
    called, it would have seen HIDDEN_BULLISH_ABSORPTION.

  Case B: price_to_vwap_pct = -0.07, whale_cvd_slope = +1500
    Gatekeeper: 0.07 > 0.05 AND 1500 > 1000 → "HIDDEN_BULLISH_ABSORPTION"
    ReasoningEngine: 0.07 < 0.1 → NO DIVERGENCE → "EQUILIBRIUM_CHOP"
    Result: Gatekeeper triggers Gate 1, authorizes LLM, but LLM sees
    EQUILIBRIUM_CHOP and may output "Wait" — token waste.

WHERE THIS MATTERS MOST:
The Gatekeeper's kinetic_divergence is used in TWO places:
  1. Gate 1 entry authorization (line 162)
  2. Whipsaw Override for active positions (lines 121-128)

The Whipsaw Override is the more dangerous one. If you have an active Long
position approaching stoploss, and whale_cvd_slope is +500 (genuine
institutional bid), the ReasoningEngine would classify this as absorption
and hold — but the Gatekeeper's stricter threshold fails to detect it,
so the Whipsaw Override doesn't fire, and the position gets stopped out.

RECOMMENDED FIX:
    # Align thresholds with the ReasoningEngine for consistency:
    if price_to_vwap_pct < -0.1 and whale_cvd_slope > 0:
        kinetic_divergence = "HIDDEN_BULLISH_ABSORPTION"
    elif price_to_vwap_pct > 0.1 and whale_cvd_slope < 0:
        kinetic_divergence = "HIDDEN_BEARISH_DISTRIBUTION"

    # This ensures the Gatekeeper and LLM always agree on divergence state.
    # The Gatekeeper is a PRE-FILTER — it should never be stricter than the
    # system it's filtering FOR, or it will silently block valid signals.


================================================================================
                          PRIORITY ORDER
================================================================================

    PRIORITY 1: Issue #2 (fillna coverage)
      Reason: Affects EVERY cold start and EVERY new symbol addition.
      The first 100 minutes of trading produce fabricated volatility states.
      Fix effort: 5 minutes.

    PRIORITY 2: Issue #1 (VTT early-session clamp)
      Reason: Affects the first 2-3 minutes of EVERY trading session.
      The opening auction volume is silently dropped.
      Fix effort: 1 line change.

    PRIORITY 3: Issue #3 (Divergence threshold alignment)
      Reason: Only affects active positions approaching stoploss with
      moderate whale flow. Rare but high-impact when it occurs.
      Fix effort: 2 line change.

================================================================================
                          END OF REPORT
================================================================================
