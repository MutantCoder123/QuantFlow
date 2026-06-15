class ConvictionScorer:
    def __init__(self):
        self.previous_bias = "NEUTRAL"
        self.polarity_flips_today = 0

    def score_setup(self, semantic_payload: dict, flat_telemetry: dict) -> dict:
        market_regime = semantic_payload.get("market_regime", {})
        regime = market_regime.get("current_regime", "TRANSITIONAL_DRIFT")
        session_phase = market_regime.get("session_phase", "UNKNOWN")

        # Step 1: Hard Rejection Guard
        if regime == "MARKET_CLOSED":
            return self._create_rejected_output(0.0, "NEUTRAL", "MARKET_CLOSED")

        # Step 2: Component Scoring (13 signals -> 4 categories)
        
        # --- Microstructure (Max abs sum: 11) ---
        micro = semantic_payload.get("1_live_microstructure", {})
        flow_div = micro.get("flow_divergence_state", "")
        obi = micro.get("order_book_imbalance_state", "")
        vol_regime = micro.get("volume_regime", "")
        cost_basis = micro.get("session_cost_basis_state", "")
        fractal = micro.get("fractal_alignment", "")
        kinetic = micro.get("kinetic_divergence", "")
        elasticity = micro.get("elasticity_risk", "")
        volatility = micro.get("volatility_state", "")

        micro_score = 0.0
        if "MOMENTUM_CONFIRMED_BULLISH" in flow_div: micro_score += 3
        elif "HIDDEN_BULLISH_ABSORPTION" in flow_div: micro_score += 2
        elif "MOMENTUM_CONFIRMED_BEARISH" in flow_div: micro_score -= 3
        elif "HIDDEN_BEARISH_DISTRIBUTION" in flow_div: micro_score -= 2

        if obi == "EXTREME_BID_DOMINANCE": micro_score += 1
        elif obi == "MODERATE_BID": micro_score += 0.5
        elif obi == "EXTREME_ASK_DOMINANCE": micro_score -= 1
        elif obi == "MODERATE_ASK": micro_score -= 0.5

        if cost_basis == "EXTREME_DISCOUNT": micro_score += 1
        elif cost_basis == "ELEVATED_DISCOUNT": micro_score += 0.5
        elif cost_basis == "EXTREME_PREMIUM": micro_score -= 1
        elif cost_basis == "ELEVATED_PREMIUM": micro_score -= 0.5

        if fractal == "STRONG_FRACTAL_BULL": micro_score += 2
        elif fractal == "WEAK_FRACTAL_BULL": micro_score += 0.5
        elif fractal == "STRONG_FRACTAL_BEAR": micro_score -= 2
        elif fractal == "WEAK_FRACTAL_BEAR": micro_score -= 0.5

        if kinetic == "BULLISH_KINETIC_DIVERGENCE": micro_score += 1
        elif kinetic == "BEARISH_KINETIC_DIVERGENCE": micro_score -= 1

        if elasticity == "OVERSTRETCHED_MEAN_REVERSION_RISK_UP": micro_score += 2
        elif elasticity == "OVERSTRETCHED_MEAN_REVERSION_RISK_DOWN": micro_score -= 2

        # OVERRIDE: Volume Multiplier
        if vol_regime == "TIME_ADJUSTED_SHOCK":
            micro_score *= 1.5

        micro_norm = max(min(micro_score / 11.0, 1.0), -1.0)  # Normalize and bound to [-1, 1]

        # --- Derivatives (Max abs sum: 4) ---
        deriv = semantic_payload.get("2_derivatives_matrix_52w", {})
        pcr = deriv.get("pcr_regime", "")
        vol_reg = deriv.get("volatility_regime_state", "")
        gravity = deriv.get("options_gravity_state", "")

        deriv_score = 0.0
        if pcr == "EXTREME_PUT_WRITING": deriv_score += 2
        elif pcr == "HEAVY_CALL_RESISTANCE": deriv_score -= 2

        if gravity == "ESCAPE_VELOCITY_ACHIEVED": deriv_score += 1
        elif gravity == "GRAVITY_MAX_IMMINENT_PULL": deriv_score -= 1
        
        deriv_norm = max(min(deriv_score / 4.0, 1.0), -1.0)

        # --- Structural Edge (Max abs sum: 4) ---
        struct = semantic_payload.get("3_local_structural_edge_20d", {})
        momentum = struct.get("momentum_confluence", {}).get("state", "")
        prox = struct.get("structural_proximity_state", {})
        prox_state = prox.get("state", "")
        prox_level = prox.get("nearest_level", "")
        prox_dir = prox.get("approach_direction", "")

        struct_score = 0.0
        if momentum == "STRONG_ALPHA": struct_score += 2
        elif momentum == "MODERATE_ALPHA": struct_score += 1
        elif momentum == "SEVERE_WEAKNESS": struct_score -= 2
        elif momentum == "MODERATE_WEAKNESS": struct_score -= 1

        if prox_state == "TEST_IMMINENT":
            if prox_level == "rolling_20d_value_area_high":
                if prox_dir == "TESTING_FROM_BELOW": struct_score -= 2
                elif prox_dir == "TESTING_FROM_ABOVE": struct_score += 1
            elif prox_level == "rolling_20d_value_area_low":
                if prox_dir == "TESTING_FROM_ABOVE": struct_score += 2
                elif prox_dir == "TESTING_FROM_BELOW": struct_score -= 1
            elif prox_level == "rolling_20d_poc_price":
                struct_score += 1

        struct_norm = max(min(struct_score / 4.0, 1.0), -1.0)

        # --- Catalyst (Max abs sum: 0.5) ---
        catalyst = semantic_payload.get("4_catalyst_engine", {})
        raw_news = catalyst.get("raw_news", [])
        catalyst_score = 0.5 if len(raw_news) > 0 else 0.0
        catalyst_norm = catalyst_score / 0.5 if catalyst_score > 0 else 0.0

        # Step 3: Regime-Adjusted Weighted Sum
        if regime == "TREND_EXPANSION":
            w_micro, w_struct, w_deriv, w_cat = 0.50, 0.30, 0.10, 0.10
        elif regime == "RANGE_BOUND_CHOP":
            w_micro, w_struct, w_deriv, w_cat = 0.20, 0.30, 0.40, 0.10
        elif regime == "PRE_BREAKOUT_SQUEEZE":
            w_micro, w_struct, w_deriv, w_cat = 0.30, 0.40, 0.20, 0.10
        elif regime == "MEAN_REVERSION_IMMINENT":
            w_micro, w_struct, w_deriv, w_cat = 0.40, 0.20, 0.30, 0.10
        else: # Default
            w_micro, w_struct, w_deriv, w_cat = 0.40, 0.25, 0.20, 0.15

        composite = (micro_norm * w_micro) + (struct_norm * w_struct) + (deriv_norm * w_deriv) + (catalyst_norm * w_cat)

        # OVERRIDE: Time-of-Day Penalty
        if session_phase == "LUNCH_CHOP":
            composite *= 0.5
            
        # OVERRIDE: The Whipsaw Shield
        candidate_bias = "NEUTRAL"
        if composite >= 0.30:
            candidate_bias = "LONG"
        elif composite <= -0.30:
            candidate_bias = "SHORT"
            
        if candidate_bias in ["LONG", "SHORT"] and self.previous_bias in ["LONG", "SHORT"] and candidate_bias != self.previous_bias:
            self.polarity_flips_today += 1
            
        if self.polarity_flips_today >= 3:
            composite *= 0.5  # Apply 50% penalty to the composite score
            self.polarity_flips_today = 0  # Reset
            # Re-evaluate candidate bias after penalty
            if composite >= 0.30: candidate_bias = "LONG"
            elif composite <= -0.30: candidate_bias = "SHORT"
            else: candidate_bias = "NEUTRAL"

        if candidate_bias != "NEUTRAL":
            self.previous_bias = candidate_bias

        composite = round(composite, 2)
        bias = candidate_bias

        # Step 4: Bias Determination
        if bias == "NEUTRAL":
            return self._create_rejected_output(composite, bias, "NEUTRAL_CONVICTION")

        # Step 5: Regime-Specific Hard Kills
        if regime == "MEAN_REVERSION_IMMINENT":
            if bias == "LONG" and "RISK_DOWN" in elasticity:
                return self._create_rejected_output(composite, bias, "MEAN_REVERSION_DIRECTIONAL_CONFLICT")
            if bias == "SHORT" and "RISK_UP" in elasticity:
                return self._create_rejected_output(composite, bias, "MEAN_REVERSION_DIRECTIONAL_CONFLICT")

        if regime == "RANGE_BOUND_CHOP" and prox_state == "TEST_IMMINENT":
             return self._create_rejected_output(composite, bias, "CHOP_PROXIMITY_CONFLICT")

        # Step 6: Dynamic Geometric Risk Gateway & Expectancy Matrix
        ltp = flat_telemetry.get("ltp")
        if not ltp or ltp <= 0:
             return self._create_rejected_output(composite, bias, "INVALID_LTP")

        cam = struct.get("camarilla_pivots", {})
        
        atr_15m = flat_telemetry.get("atr_15m")
        if not atr_15m or atr_15m <= 0:
            atr_15m = ltp * 0.0025  # 0.25% fallback

        atr_5m = flat_telemetry.get("atr_5m")
        if not atr_5m or atr_5m <= 0:
            atr_5m = ltp * 0.0010  # 0.10% fallback
        
        levels = [
            flat_telemetry.get("session_vwap"),
            flat_telemetry.get("rolling_20d_poc_price"),
            flat_telemetry.get("rolling_20d_value_area_high"),
            flat_telemetry.get("rolling_20d_value_area_low"),
            cam.get("H3"), cam.get("H4"), cam.get("L3"), cam.get("L4")
        ]

        valid_levels = [lvl for lvl in levels if lvl is not None and lvl > 0]
        nearest_ceiling = min([lvl for lvl in valid_levels if lvl > ltp], default=float('inf'))
        nearest_floor = max([lvl for lvl in valid_levels if lvl < ltp], default=0.0)

        if nearest_ceiling == float('inf') or nearest_floor == 0.0:
            return self._create_rejected_output(composite, bias, "MISSING_STRUCTURAL_BOUNDARIES")

        vol_state = deriv.get("volatility_regime_state", "")
        if vol_state == 'EXTREME_EXPANSION': atr_mult = 1.0
        elif vol_state == 'ELEVATED_VOLATILITY': atr_mult = 0.75
        elif vol_state == 'PREMIUM_COMPRESSION': atr_mult = 0.30
        else: atr_mult = 0.50

        if bias == "LONG":
            target = nearest_ceiling
            padded_stop = nearest_floor - (atr_mult * atr_15m)
        else: # SHORT
            target = nearest_floor
            padded_stop = nearest_ceiling + (atr_mult * atr_15m)

        risk = abs(ltp - padded_stop)
        reward = abs(target - ltp)

        effective_risk = risk + (0.1 * atr_5m)
        effective_reward = max(0.0001, reward - (0.1 * atr_5m))

        import math
        # Multiply absolute score by 3.0 to stretch the logistic curve
        raw_prob = 1 / (1 + math.exp(-(abs(composite) * 3.0)))
        # Dampen slightly to cap absolute perfection at ~84%
        p_implied = 0.50 + ((raw_prob - 0.50) * 0.75)
        
        p_breakeven = effective_risk / (effective_risk + effective_reward)
        stat_edge = p_implied - p_breakeven

        if stat_edge < 0.05:
             return self._create_rejected_output(
                 composite, bias, "INSUFFICIENT_STATISTICAL_EDGE",
                 geometry={"calculated_entry": round(ltp, 2), "padded_stop": round(padded_stop, 2), "calculated_target": round(target, 2), "effective_risk": round(effective_risk, 2), "effective_reward": round(effective_reward, 2)},
                 expectancy_matrix={"implied_probability": round(p_implied, 2), "breakeven_probability": round(p_breakeven, 2), "statistical_edge": round(stat_edge, 2)}
             )

        # Step 7: Output
        return {
            "directional_bias": bias,
            "composite_score": composite,
            "setup_rejected": False,
            "rejection_reason": None,
            "execution_geometry": {
                "calculated_entry": round(ltp, 2),
                "padded_stop": round(padded_stop, 2),
                "calculated_target": round(target, 2),
                "effective_risk": round(effective_risk, 2),
                "effective_reward": round(effective_reward, 2)
            },
            "expectancy_matrix": {
                "implied_probability": round(p_implied, 2),
                "breakeven_probability": round(p_breakeven, 2),
                "statistical_edge": round(stat_edge, 2)
            }
        }

    def _create_rejected_output(self, score: float, bias: str, reason: str, geometry: dict = None, expectancy_matrix: dict = None) -> dict:
        return {
            "directional_bias": bias,
            "composite_score": score,
            "setup_rejected": True,
            "rejection_reason": reason,
            "execution_geometry": geometry,
            "expectancy_matrix": expectancy_matrix
        }

class ConvictionScorerRegistry:
    _scorers: dict[str, ConvictionScorer] = {}

    @classmethod
    def get_or_create(cls, symbol: str) -> ConvictionScorer:
        if symbol not in cls._scorers:
            cls._scorers[symbol] = ConvictionScorer()
        return cls._scorers[symbol]
