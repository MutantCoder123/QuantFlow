class ConvictionScorer:
    def __init__(self):
        self.previous_bias = "NEUTRAL"
        self.polarity_flips_today = 0

    def _get_adaptive_weights(self, regime: str) -> dict:
        base_weights = {
            "TREND_EXPANSION": {"w_micro": 0.45, "w_struct": 0.15, "w_deriv": 0.15, "w_cat": 0.25},
            "VOLATILITY_EXPANSION": {"w_micro": 0.50, "w_struct": 0.30, "w_deriv": 0.10, "w_cat": 0.10},
            "RANGE_BOUND_CHOP": {"w_micro": 0.20, "w_struct": 0.30, "w_deriv": 0.40, "w_cat": 0.10},
            "PRE_BREAKOUT_SQUEEZE": {"w_micro": 0.30, "w_struct": 0.40, "w_deriv": 0.20, "w_cat": 0.10},
            "MEAN_REVERSION_IMMINENT": {"w_micro": 0.40, "w_struct": 0.20, "w_deriv": 0.30, "w_cat": 0.10},
            "DEFAULT": {"w_micro": 0.40, "w_struct": 0.25, "w_deriv": 0.20, "w_cat": 0.15}
        }
        
        base = base_weights.get(regime, base_weights["DEFAULT"])
        
        try:
            from performance_analyzer import PerformanceAnalyzer
            feedback = PerformanceAnalyzer.get_feedback_payload(last_n_days=14)
            if not feedback or feedback.get("total_signals", 0) < 30:
                return base
            
            regime_stats = feedback.get("regime_accuracy", {}).get(regime)
            if not regime_stats or regime_stats.get("total_resolved", 0) < 10:
                return base
            
            win_rate = regime_stats["win_rate_30m"] / 100.0
            scale = 0.8 + (win_rate - 0.4) * (0.4 / 0.3)
            scale = max(0.7, min(1.3, scale))
            
            adjusted = dict(base)
            dominant_key = max(base, key=base.get)
            adjusted[dominant_key] = base[dominant_key] * scale
            
            total = sum(adjusted.values())
            return {k: round(v / total, 3) for k, v in adjusted.items()}
            
        except Exception:
            return base

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

        micro_norm = max(min(micro_score / 5.0, 1.0), -1.0)  # Normalize and bound to [-1, 1]

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
        
        deriv_norm = max(min(deriv_score / 3.0, 1.0), -1.0)

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

        struct_norm = max(min(struct_score / 3.0, 1.0), -1.0)

        # --- Catalyst (Max abs sum: 0.5) ---
        catalyst = semantic_payload.get("4_catalyst_engine", {})
        raw_news = catalyst.get("raw_news", [])
        catalyst_norm = 0.0  # Pass-through logic: qualitative news interpretation belongs in LLM Layer 2

        # Step 3: Composite Calculation (Adaptive Feedback)
        weights = self._get_adaptive_weights(regime)
        w_micro, w_struct, w_deriv, w_cat = weights["w_micro"], weights["w_struct"], weights["w_deriv"], weights["w_cat"]

        composite = (micro_norm * w_micro) + (struct_norm * w_struct) + (deriv_norm * w_deriv) + (catalyst_norm * w_cat)

        # OVERRIDE: The Whipsaw Shield
        candidate_bias = "NEUTRAL"
        if composite >= 0.15:
            candidate_bias = "LONG"
        elif composite <= -0.15:
            candidate_bias = "SHORT"
            
        if candidate_bias in ["LONG", "SHORT"] and self.previous_bias in ["LONG", "SHORT"] and candidate_bias != self.previous_bias:
            self.polarity_flips_today += 1
            
        if self.polarity_flips_today >= 3:
            composite *= 0.5  # Apply 50% penalty to the composite score
            self.polarity_flips_today = 0  # Reset
            # Re-evaluate candidate bias after penalty
            if composite >= 0.15: candidate_bias = "LONG"
            elif composite <= -0.15: candidate_bias = "SHORT"
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
            if bias == "LONG" and prox_dir == "TESTING_FROM_BELOW" and "value_area_high" in prox_level:
                return self._create_rejected_output(composite, bias, "CHOP_PROXIMITY_CONFLICT")
            elif bias == "SHORT" and prox_dir == "TESTING_FROM_ABOVE" and "value_area_low" in prox_level:
                return self._create_rejected_output(composite, bias, "CHOP_PROXIMITY_CONFLICT")

        # Step 6: Dynamic Geometric Risk Gateway & Expectancy Matrix
        ltp = flat_telemetry.get("ltp")
        if not ltp or ltp <= 0:
             return self._create_rejected_output(composite, bias, "INVALID_LTP")

        cam = struct.get("camarilla_pivots", {})
        
        atr_15m = flat_telemetry.get("atr_15m")
        if not atr_15m or atr_15m <= 0:
            atr_15m = ltp * 0.0050  # 0.50% fallback

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

        if nearest_ceiling == float('inf'):
            nearest_ceiling = ltp + (2.0 * atr_15m)
        if nearest_floor == 0.0:
            nearest_floor = ltp - (2.0 * atr_15m)

        vol_state = deriv.get("volatility_regime_state", "")
        if vol_state == 'EXTREME_EXPANSION': atr_mult = 1.0
        elif vol_state == 'ELEVATED_VOLATILITY': atr_mult = 0.75
        elif vol_state == 'PREMIUM_COMPRESSION': atr_mult = 0.30
        else: atr_mult = 0.50

        if bias == "LONG":
            ceilings = sorted([lvl for lvl in valid_levels if lvl > ltp])
            if regime in ("TREND_EXPANSION", "PRE_BREAKOUT_SQUEEZE") and len(ceilings) >= 2:
                target = ceilings[1]
            elif ceilings:
                target = ceilings[0]
            else:
                target = ltp + (2.0 * atr_15m)
                
            min_reward = 1.5 * atr_15m
            if abs(target - ltp) < min_reward:
                target = ltp + min_reward
                
            calculated_entry = ltp - (0.2 * atr_5m)
            padded_stop = nearest_floor - (atr_mult * atr_15m)
        else: # SHORT
            floors = sorted([lvl for lvl in valid_levels if lvl < ltp], reverse=True)
            if regime in ("TREND_EXPANSION", "PRE_BREAKOUT_SQUEEZE") and len(floors) >= 2:
                target = floors[1]
            elif floors:
                target = floors[0]
            else:
                target = ltp - (2.0 * atr_15m)
                
            min_reward = 1.5 * atr_15m
            if abs(target - ltp) < min_reward:
                target = ltp - min_reward
                
            calculated_entry = ltp + (0.2 * atr_5m)
            padded_stop = nearest_ceiling + (atr_mult * atr_15m)

        risk = abs(calculated_entry - padded_stop)
        reward = abs(target - calculated_entry)

        effective_risk = risk + (0.1 * atr_5m)
        effective_reward = max(0.0001, reward - (0.1 * atr_5m))

        import math
        # Multiply absolute score by 4.5 to stretch the logistic curve
        raw_prob = 1 / (1 + math.exp(-(abs(composite) * 4.5)))
        # Dampen slightly to cap absolute perfection at ~92%
        p_implied = 0.50 + ((raw_prob - 0.50) * 0.85)
        
        p_breakeven = effective_risk / (effective_risk + effective_reward)
        stat_edge = p_implied - p_breakeven

        if stat_edge < 0.05:
             return self._create_rejected_output(
                 composite, bias, "INSUFFICIENT_STATISTICAL_EDGE",
                 geometry={"calculated_entry": round(calculated_entry, 2), "padded_stop": round(padded_stop, 2), "calculated_target": round(target, 2), "effective_risk": round(effective_risk, 2), "effective_reward": round(effective_reward, 2)},
                 expectancy_matrix={"implied_probability": round(p_implied, 2), "breakeven_probability": round(p_breakeven, 2), "statistical_edge": round(stat_edge, 2)}
             )

        # Step 7: Output
        return {
            "directional_bias": bias,
            "composite_score": composite,
            "setup_rejected": False,
            "rejection_reason": None,
            "execution_geometry": {
                "calculated_entry": round(calculated_entry, 2),
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
