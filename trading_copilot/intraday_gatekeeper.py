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
    def evaluate(cls, payload: Dict[str, Any], user_context: Dict[str, Any], ltp: float) -> Dict[str, Any]:
        # 1. THE GHOST DATA SHIELD
        market_state = payload.get("market_state", "LIVE")
        if market_state != "LIVE":
            return {"Action": "Wait", "Priority_Score": 0, "Confidence_Score": 0, "llm_authorized": False}

        # 2. TIME OVERRIDE
        # If local machine time >= 15:15 IST (Auto-Square off time for intraday)
        current_time_utc = datetime.datetime.utcnow()
        ist_offset = datetime.timedelta(hours=5, minutes=30)
        current_time_ist = current_time_utc + ist_offset
        
        position = user_context.get("position", {}) if user_context else {}
        
        if current_time_ist.hour >= 15 and current_time_ist.minute >= 15:
            # Force override
            return cls._create_response("Close", priority=10, confidence=10, llm_auth=False)
            
        # Extract Local Math Metrics
        vol_z_score_5m = 0.0
        whale_cvd_ema_1h = 0.0
        distance_to_poc_pct = 0.0
        volume_poc_price = 0.0
        kinetic_divergence = ""
        price_to_vwap_pct = 100.0
        flow_regime = ""
        
        try:
            # Safely navigate nested payload structures for injected metrics
            struct = payload.get("structured_payload", payload)
            micro = struct.get("1_live_microstructure", {}).get("order_flow", {})
            vol_z_score_5m = float(micro.get("vol_z_score_5m", 0.0))
            whale_cvd_ema_1h = float(micro.get("whale_cvd_ema_1h", 0.0))
            flow_regime = micro.get("flow_regime", "")
            
            macro = struct.get("3_macro_statistical_edge_5y", {}).get("structural_liquidity", {})
            distance_to_poc_pct = float(macro.get("distance_to_poc_pct", 100.0))
            volume_poc_price = float(macro.get("volume_poc_price", 0.0))
            
            kd_obj = struct.get("1_live_microstructure", {}).get("mtf_technicals", {}).get("kinetic_divergence", {})
            if isinstance(kd_obj, dict):
                kinetic_divergence = kd_obj.get("divergence_state", "")
            elif isinstance(kd_obj, str):
                kinetic_divergence = kd_obj
                
            price_to_vwap_pct = float(payload.get("price_to_vwap_pct", micro.get("price_to_vwap_pct", 100.0)))
        except (ValueError, TypeError):
            pass

        # 3. STATE 1: ACTIVE POSITION
        if position:
            entry_timestamp = position.get("entry_timestamp", time.time())
            time_in_trade_minutes = (time.time() - entry_timestamp) / 60.0
            
            entry_price = float(position.get("entry_price", ltp))
            direction = position.get("direction", "Long")
            stoploss = position.get("stoploss")
            
            # Calculate PnL
            if direction == "Long":
                pnl_pct = ((ltp - entry_price) / entry_price) * 100 if entry_price > 0 else 0
            else:
                pnl_pct = ((entry_price - ltp) / entry_price) * 100 if entry_price > 0 else 0
                
            # CRITICAL EXIT GATE
            stop_proximity_hit = False
            if stoploss and float(stoploss) > 0:
                dist_to_sl_pct = abs(ltp - float(stoploss)) / ltp * 100
                if dist_to_sl_pct <= 0.5:
                    stop_proximity_hit = True
                    
            # Whipsaw Override
            if stop_proximity_hit:
                if direction == "Long" and kinetic_divergence == "HIDDEN_BULLISH_ABSORPTION":
                    res = cls._create_response("Hold", priority=4, confidence=5, llm_auth=False)
                    res["Alert_Log"] = "Whipsaw Filtered: Institutional absorption holding the VWAP line."
                    return res
                elif direction == "Short" and kinetic_divergence == "HIDDEN_BEARISH_DISTRIBUTION":
                    res = cls._create_response("Hold", priority=4, confidence=5, llm_auth=False)
                    res["Alert_Log"] = "Whipsaw Filtered: Institutional distribution suppressing the VWAP line."
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
            
        # 4. STATE 0: IDLE (No Active Position)
        
        # VETO OVERRIDE: Stealth Accumulation
        if flow_regime == "ABSORPTION_BUYING" and abs(price_to_vwap_pct) <= 0.05:
            res = cls._create_response("Long", priority=10, confidence=9, llm_auth=True)
            res["Alert_Log"] = "VETO: Stealth accumulation detected at VWAP. Bypassing volume gates."
            return res
        
        # GATE 1: THE WHALE SWEEP (VWAP Divergence)
        gate_1_active = vol_z_score_5m >= 2.0 and kinetic_divergence in ["HIDDEN_BULLISH_ABSORPTION", "HIDDEN_BEARISH_DISTRIBUTION"]
        
        # GATE 2: SESSION VWAP INTERACTION
        gate_2_active = abs(price_to_vwap_pct) <= 0.2 and vol_z_score_5m >= 1.5 and abs(whale_cvd_ema_1h) > 0
        
        # GATE 3: THE MACRO WALL BOUNCE
        gate_3_active = distance_to_poc_pct <= 1.0 and vol_z_score_5m >= 1.5
        
        if gate_1_active or gate_2_active or gate_3_active:
            action = "Long" if whale_cvd_ema_1h > 0 else "Short"
            return cls._create_response(action, priority=8, confidence=7, llm_auth=True)
            
        # NONE MET (Wait)
        return cls._create_response("Wait", priority=3, confidence=3, llm_auth=False)
