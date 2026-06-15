class SemanticTagger:
    """
    Translates raw Flat Telemetry JSON into heavily compressed, text-optimized 
    Structured JSON payloads for the LLM. Prevents aggressive dimensionality 
    reduction by mapping statistical anomalies (Z-scores/Percentiles) and 
    structural proximities rather than arbitrary absolute floats.
    """

    @classmethod
    def translate_to_llm_payload(cls, flat: dict) -> dict:
        payload = {}
        
        ltp = float(flat.get("ltp") or 1.0)
        market_state = flat.get("market_state", "LIVE")
        
        # MARKET STATE GUARD (Flaw 10)
        # If market is closed, zero out live microstructure to prevent phantom signals
        if market_state == "CLOSED":
            whale_cvd_live = 0.0
            vol_z_score_5m = 0.0
            obi = 0.0
            price_to_vwap_pct = 0.0
            whale_cvd_slope = 0.0
        else:
            whale_cvd_live = float(flat.get("whale_cvd_live", 0.0))
            vol_z_score_5m = float(flat.get("vol_z_score_5m", 0.0))
            obi = float(flat.get("obi", 0.0))
            price_to_vwap_pct = float(flat.get("price_to_vwap_pct", 0.0))
            whale_cvd_slope = float(flat.get("whale_cvd_slope", 0.0))
            
        # ATR EXTRACT & FAILSAFES
        atr_1d = flat.get("atr_1d")
        if not atr_1d or atr_1d <= 0:
            atr_1d = ltp * 0.015 
            
        atr_15m = flat.get("atr_15m")
        if not atr_15m or atr_15m <= 0:
            atr_15m = ltp * 0.0025 

        atr_5m = flat.get("atr_5m")
        if not atr_5m or atr_5m <= 0:
            atr_5m = ltp * 0.0015
            
        atr_pct_15m = (atr_15m / ltp) * 100
        atr_pct_5m = (atr_5m / ltp) * 100
        atr_pct_1d = (atr_1d / ltp) * 100
        
        # ---------------------------------------------------------------------
        # BLOCK 1: LIVE MICROSTRUCTURE
        # ---------------------------------------------------------------------
        
        # session_cost_basis_state
        ratio_vwap = price_to_vwap_pct / atr_pct_15m if atr_pct_15m > 0 else 0
        if ratio_vwap > 1.5:
            session_cost_basis_state = "EXTREME_PREMIUM"
        elif ratio_vwap > 0.75:
            session_cost_basis_state = "ELEVATED_PREMIUM"
        elif ratio_vwap < -1.5:
            session_cost_basis_state = "EXTREME_DISCOUNT"
        elif ratio_vwap < -0.75:
            session_cost_basis_state = "ELEVATED_DISCOUNT"
        else:
            session_cost_basis_state = "AT_EQUILIBRIUM"
            
        # flow_divergence_state (Magnitude Gated)
        if abs(price_to_vwap_pct) > (0.4 * atr_pct_15m) and abs(whale_cvd_slope) > 50:
            if price_to_vwap_pct > 0 and whale_cvd_slope < 0:
                flow_divergence_state = "HIDDEN_BEARISH_DISTRIBUTION"
            elif price_to_vwap_pct < 0 and whale_cvd_slope > 0:
                flow_divergence_state = "HIDDEN_BULLISH_ABSORPTION"
            elif price_to_vwap_pct > 0 and whale_cvd_slope > 0:
                flow_divergence_state = "MOMENTUM_CONFIRMED_BULLISH"
            elif price_to_vwap_pct < 0 and whale_cvd_slope < 0:
                flow_divergence_state = "MOMENTUM_CONFIRMED_BEARISH"
            else:
                flow_divergence_state = "EQUILIBRIUM_CHOP"
        else:
            flow_divergence_state = "EQUILIBRIUM_CHOP"
            
        # volume_regime
        if vol_z_score_5m > 2.5:
            volume_regime = "TIME_ADJUSTED_SHOCK"
        elif vol_z_score_5m > 1.0:
            volume_regime = "ELEVATED_ACCUMULATION"
        elif vol_z_score_5m < -1.0:
            volume_regime = "SUPPRESSED_FLOW"
        else:
            volume_regime = "NORMAL_DRIFT"
            
        # order_book_imbalance_state
        if obi > 0.6:
            order_book_imbalance_state = "EXTREME_BID_DOMINANCE"
        elif obi > 0.2:
            order_book_imbalance_state = "MODERATE_BID"
        elif obi < -0.6:
            order_book_imbalance_state = "EXTREME_ASK_DOMINANCE"
        elif obi < -0.2:
            order_book_imbalance_state = "MODERATE_ASK"
        else:
            order_book_imbalance_state = "BALANCED"
            
        block_1 = {
            "session_cost_basis_state": session_cost_basis_state,
            "volume_regime": volume_regime,
            "flow_divergence_state": flow_divergence_state,
            "order_book_imbalance_state": order_book_imbalance_state,
            "fractal_alignment": flat.get("fractal_alignment", "CONFLICTING_CHOP"),
            "elasticity_risk": flat.get("elasticity_risk", "EQUILIBRIUM"),
            "kinetic_divergence": flat.get("kinetic_divergence", "MOMENTUM_CONFIRMED"),
            "volatility_state": flat.get("volatility_state", "NORMAL_RANGING")
        }

        # ---------------------------------------------------------------------
        # BLOCK 2: DERIVATIVES & GRAVITY
        # ---------------------------------------------------------------------
        
        iv_pct = flat.get("iv_percentile_52w")
        if iv_pct is not None:
            if iv_pct > 90:
                volatility_regime_state = "EXTREME_EXPANSION"
            elif iv_pct > 75:
                volatility_regime_state = "ELEVATED_VOLATILITY"
            elif iv_pct < 25:
                volatility_regime_state = "PREMIUM_COMPRESSION"
            else:
                volatility_regime_state = "NORMAL_PRICING"
        else:
            volatility_regime_state = "NORMAL_PRICING"
            
        max_pain = flat.get("max_pain_price")
        if max_pain:
            dist_to_mp = abs(ltp - max_pain)
            ratio_mp = dist_to_mp / atr_1d
            if ratio_mp < 0.3:
                options_gravity_state = "GRAVITY_MAX_IMMINENT_PULL"
            elif ratio_mp > 2.0:
                options_gravity_state = "ESCAPE_VELOCITY_ACHIEVED"
            else:
                options_gravity_state = "NORMAL_ORBIT"
        else:
            options_gravity_state = "NORMAL_ORBIT"
            
        pcr_pct = flat.get("pcr_percentile_52w")
        if pcr_pct is not None:
            if pcr_pct > 80:
                pcr_regime = "EXTREME_PUT_WRITING"
            elif pcr_pct < 20:
                pcr_regime = "HEAVY_CALL_RESISTANCE"
            else:
                pcr_regime = "NORMAL"
        else:
            pcr_regime = "NORMAL"
            
        block_2 = {
            "volatility_regime_state": volatility_regime_state,
            "options_gravity_state": options_gravity_state,
            "pcr_regime": pcr_regime
        }

        # ---------------------------------------------------------------------
        # BLOCK 3: STRUCTURAL EDGE
        # ---------------------------------------------------------------------
        
        levels = {
            "rolling_20d_poc_price": flat.get("rolling_20d_poc_price"),
            "rolling_20d_value_area_high": flat.get("rolling_20d_value_area_high"),
            "rolling_20d_value_area_low": flat.get("rolling_20d_value_area_low")
        }
        
        nearest_name = None
        nearest_dist = float('inf')
        nearest_val = None
        for name, val in levels.items():
            if val is not None and val > 0:
                d = abs(ltp - val)
                if d < nearest_dist:
                    nearest_dist = d
                    nearest_name = name
                    nearest_val = val
                    
        structural_proximity = {"state": "NO_MANS_LAND", "nearest_level": None, "approach_direction": None, "distance_pct": None}
        if nearest_name:
            structural_proximity["nearest_level"] = nearest_name
            structural_proximity["distance_pct"] = round((nearest_dist / ltp) * 100, 3)
            structural_proximity["approach_direction"] = "TESTING_FROM_ABOVE" if ltp > nearest_val else "TESTING_FROM_BELOW"
            
            if nearest_dist < (0.5 * atr_1d):
                structural_proximity["state"] = "TEST_IMMINENT"
            elif nearest_dist < atr_1d:
                structural_proximity["state"] = "APPROACHING"
                
        alpha_5d = flat.get("alpha_vs_nifty_5d", 0.0)
        if alpha_5d > 2.0:
            alpha_state = "STRONG_ALPHA"
        elif alpha_5d > 0.5:
            alpha_state = "MODERATE_ALPHA"
        elif alpha_5d < -2.0:
            alpha_state = "SEVERE_WEAKNESS"
        elif alpha_5d < -0.5:
            alpha_state = "MODERATE_WEAKNESS"
        else:
            alpha_state = "PERFORMER"
            
        momentum_confluence = {
            "state": alpha_state,
            "alpha_5d_pct": alpha_5d
        }

        block_3 = {
            "structural_proximity_state": structural_proximity,
            "momentum_confluence": momentum_confluence,
            "key_geometry": flat.get("geometry", {}),
            "candlestick_patterns": flat.get("candlesticks", {}),
            "camarilla_pivots": flat.get("camarilla", {})
        }

        # ---------------------------------------------------------------------
        # ASSEMBLE HIERARCHY
        # ---------------------------------------------------------------------
        payload = {
            "market_state": market_state,
            "symbol": flat.get("symbol", "UNKNOWN"),
            "timestamp": flat.get("timestamp", 0),
            "current_time": flat.get("current_time", ""),
            "ltp": ltp,
            "prev_close": flat.get("prev_close", 0.0),
            "user_context": flat.get("user_context", {}),
            "global_market_context": flat.get("global_market_context", None),
            "1_live_microstructure": block_1,
            "2_derivatives_matrix_52w": block_2,
            "3_local_structural_edge_20d": block_3,
            "4_catalyst_engine": {
                "raw_news": flat.get("raw_news", [])
            }
        }
        
        return payload
