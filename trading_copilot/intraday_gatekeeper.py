import time
import datetime
from typing import Dict, Any, Optional

class IntradayGatekeeper:
    """
    A stateless, deterministic mathematical sieve that processes streaming market data.
    It filters noise, calculates local execution risk parameters, and determines if a 
    high-conviction event warrants triggering the external LLM reasoning engine.
    """

    @staticmethod
    def _create_response(action: str, 
                         priority: int, 
                         confidence: int, 
                         llm_auth: bool, 
                         entry: Optional[float] = None, 
                         sl: Optional[float] = None, 
                         tp: Optional[float] = None, 
                         risk: float = 0.0) -> Dict[str, Any]:
        return {
            "Action": action,
            "Entry_Target_Price": entry,
            "Stoploss": sl,
            "Exit_Target_Price": tp,
            "Confidence_Score": confidence,
            "Risk_Percentage": risk,
            "Priority_Score": priority,
            "llm_authorized": llm_auth
        }

    @classmethod
    def evaluate(cls, structured_payload: Dict[str, Any], raw_payload: Dict[str, Any], user_context: Dict[str, Any], ltp: float) -> Dict[str, Any]:
        # 1. THE GHOST DATA SHIELD
        from pipeline_guard import is_market_open
        market_state = "LIVE" if is_market_open() else "CLOSED"
        if market_state != "LIVE":
            return {"Action": "Wait", "Priority_Score": 0, "Confidence_Score": 0, "llm_authorized": False}

        # 2. TIME OVERRIDE
        # If local machine time >= 15:15 IST (Auto-Square off time for intraday)
        current_time_utc = datetime.datetime.utcnow()
        ist_offset = datetime.timedelta(hours=5, minutes=30)
        current_time_ist = current_time_utc + ist_offset
        
        position = user_context.get("position", {}) if user_context else {}
        
        current_decimal = current_time_ist.hour + current_time_ist.minute / 60.0
        if current_decimal >= 15.333:  # 15:20 IST
            # Force override
            return cls._create_response("Close", priority=10, confidence=10, llm_auth=False)
            
        # Extract Structured Intelligence
        micro = structured_payload.get("1_live_microstructure", {})
        math_setup = structured_payload.get("math_setup", {})
        regime = structured_payload.get("market_regime", {})
        current_regime = regime.get("current_regime", "TRANSITIONAL_DRIFT")
        session_phase = regime.get("session_phase", "UNKNOWN")
        
        # Read SemanticTagger's clean states
        flow_divergence = micro.get("flow_divergence_state", "EQUILIBRIUM_CHOP")
        
        # Read raw whale CVD for polarity check (not available in semantic payload)
        try:
            whale_cvd_ema_1h = float(raw_payload.get("whale_cvd_ema_1h", 0.0))
        except (ValueError, TypeError):
            whale_cvd_ema_1h = 0.0

        # 3. PATH A: ACTIVE POSITION MANAGEMENT
        if position:
            entry_timestamp = position.get("entry_timestamp", time.time())
            time_in_trade_minutes = (time.time() - entry_timestamp) / 60.0
            
            try:
                entry_price = float(position.get("entry_price", ltp))
            except (ValueError, TypeError):
                entry_price = ltp
                
            direction = position.get("direction", "Long")
            stoploss = position.get("stoploss")
            
            # Calculate PnL
            if direction == "Long":
                pnl_pct = ((ltp - entry_price) / entry_price) * 100 if entry_price > 0 else 0
            else:
                pnl_pct = ((entry_price - ltp) / entry_price) * 100 if entry_price > 0 else 0
                
            # CRITICAL EXIT GATE: Stop Proximity
            stop_proximity_hit = False
            if stoploss and float(stoploss) > 0:
                dist_to_sl_pct = abs(ltp - float(stoploss)) / ltp * 100
                if dist_to_sl_pct <= 0.5:
                    stop_proximity_hit = True
                    
            # Whipsaw Override (using SemanticTagger state)
            if stop_proximity_hit:
                if direction == "Long" and flow_divergence == "HIDDEN_BULLISH_ABSORPTION":
                    res = cls._create_response("Hold", priority=4, confidence=5, llm_auth=False)
                    res["Alert_Log"] = "Whipsaw Filtered: SemanticTagger confirms institutional absorption."
                    return res
                elif direction == "Short" and flow_divergence == "HIDDEN_BEARISH_DISTRIBUTION":
                    res = cls._create_response("Hold", priority=4, confidence=5, llm_auth=False)
                    res["Alert_Log"] = "Whipsaw Filtered: SemanticTagger confirms institutional distribution."
                    return res
                    
            # If whale_cvd_ema_1h completely flips polarity against the trade direction
            polarity_flipped = False
            if direction == "Long" and whale_cvd_ema_1h < 0:
                polarity_flipped = True
            elif direction == "Short" and whale_cvd_ema_1h > 0:
                polarity_flipped = True
                
            if stop_proximity_hit or polarity_flipped:
                return cls._create_response("Close", priority=10, confidence=9, llm_auth=True)
                
            # FAILURE TO LAUNCH GATE
            if time_in_trade_minutes > 45 and -0.5 <= pnl_pct <= 0.1:
                return cls._create_response("Wait", priority=7, confidence=6, llm_auth=True)
                
            # STAGNATION GATE
            drift_pct = abs(ltp - entry_price) / entry_price * 100 if entry_price > 0 else 0
            if time_in_trade_minutes > 30 and drift_pct < 0.2:
                # Suppress LLM to save tokens
                return cls._create_response("Hold", priority=2, confidence=5, llm_auth=False)
                
            # Default Active State: Hold without triggering LLM to save tokens
            return cls._create_response("Hold", priority=1, confidence=5, llm_auth=False)
            
        # 4. PATH B: ENTRY EVALUATION (No Active Position)
        
        # Read ConvictionScorer output directly
        setup_rejected = math_setup.get("setup_rejected", True)
        composite_score = abs(math_setup.get("composite_score", 0.0))
        bias = math_setup.get("directional_bias", "NEUTRAL")
        stat_edge = (math_setup.get("expectancy_matrix") or {}).get("statistical_edge", 0.0)
        
        # Extract Geometry and Dynamic Scaling
        geo = math_setup.get("execution_geometry") or {}
        entry = geo.get("calculated_entry")
        sl = geo.get("padded_stop")
        tp = geo.get("calculated_target")
        risk_pct = 0.0
        if entry and geo.get("effective_risk"):
            risk_pct = round((geo["effective_risk"] / entry) * 100, 2)
            
        dyn_priority = min(10, int(composite_score * 20))
        dyn_confidence = min(10, int(stat_edge * 33)) if stat_edge > 0 else 0
        
        # If ConvictionScorer rejected the setup → suppress LLM
        if setup_rejected:
            rejection = math_setup.get("rejection_reason", "UNKNOWN")
            res = cls._create_response(bias, priority=dyn_priority, confidence=dyn_confidence, llm_auth=False, entry=entry, sl=sl, tp=tp, risk=risk_pct)
            res["math_rejection"] = rejection
            return res
            
        # REGIME-AWARE SUPPRESSION
        effective_score = composite_score
        if session_phase == "LUNCH_CHOP":
            vol_regime = micro.get("volume_regime", "NORMAL_DRIFT")
            if vol_regime in ("TIME_ADJUSTED_SHOCK", "ELEVATED_ACCUMULATION"):
                effective_score *= 0.85  # High volume during lunch = real move
            else:
                effective_score *= 0.65  # Normal lunch = moderate dampening
        elif current_regime == "RANGE_BOUND_CHOP":
            effective_score *= 0.6
        elif current_regime == "TRANSITIONAL_DRIFT":
            effective_score *= 0.7
            
        # ENTRY GATE: Authorize LLM only if effective score survives regime dampening
        if effective_score >= 0.15 and stat_edge >= 0.05:
            action = "Long" if bias == "LONG" else "Short"
            return cls._create_response(action, priority=dyn_priority, confidence=dyn_confidence, llm_auth=True, entry=entry, sl=sl, tp=tp, risk=risk_pct)
            
        # NONE MET (Wait)
        res = cls._create_response(bias, priority=dyn_priority, confidence=dyn_confidence, llm_auth=False, entry=entry, sl=sl, tp=tp, risk=risk_pct)
        res["math_rejection"] = f"REGIME_DAMPENED_{session_phase}"
        return res
