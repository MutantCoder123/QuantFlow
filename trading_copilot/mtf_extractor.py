import math
import numpy as np
import pandas as pd

def sanitize_for_json(obj):
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_for_json(v) for v in obj]
    elif isinstance(obj, float):
        if math.isnan(obj) or np.isnan(obj) or math.isinf(obj):
            return None
        return obj
    elif pd.isna(obj):
        return None
    return obj

class MTFFeatureExtractor:
    @staticmethod
    def calc_fractal_alignment(payload: dict) -> str:
        score = 0
        weights = {'1d': 35, '4h': 25, '1h': 20, '30m': 10, '15m': 10}
        
        for tf, w in weights.items():
            ema9 = payload.get(f'ema_9_{tf}', 0)
            ema21 = payload.get(f'ema_21_{tf}', 0)
            if ema9 > 0 and ema21 > 0:
                if ema9 > ema21:
                    score += w
                elif ema9 < ema21:
                    score -= w
                    
        if score > 80:
            return "STRONG_FRACTAL_BULL"
        elif score < -80:
            return "STRONG_FRACTAL_BEAR"
        elif -20 <= score <= 20:
            return "CONFLICTING_CHOP"
        elif score > 0:
            return "WEAK_FRACTAL_BULL"
        else:
            return "WEAK_FRACTAL_BEAR"

    @staticmethod
    def calc_volatility_state(payload: dict, ltp: float) -> str:
        if ltp <= 0: return "NORMAL_RANGING"
        
        for tf in ['15m', '30m', '1h']:
            bbu = payload.get(f'bb_upper_{tf}', 0)
            bbl = payload.get(f'bb_lower_{tf}', 0)
            atr = payload.get(f'atr_{tf}', 0)
            
            if bbu > 0 and bbl > 0 and atr > 0:
                bandwidth = (bbu - bbl) / ltp
                # If bandwidth is exceptionally tight relative to ATR
                # Actually, bandwidth itself is a ratio. Let's compare raw distance to ATR
                dist = bbu - bbl
                if dist < (atr * 1.5):
                    if tf in ['15m', '30m']:
                        return "15M_30M_COILING_SQUEEZE"
                    else:
                        return "1H_COILING_SQUEEZE"
                elif dist > (atr * 3.0):
                    return f"{tf.upper()}_VOLATILITY_EXPANSION"
                        
        return "NORMAL_RANGING"

    @staticmethod
    def calc_elasticity_risk(payload: dict, ltp: float) -> str:
        if ltp <= 0: return "EQUILIBRIUM"
        
        ema_21_1h = payload.get('ema_21_1h', 0)
        atr_1h = payload.get('atr_1h', 0)
        
        if ema_21_1h > 0 and atr_1h > 0:
            dist = ltp - ema_21_1h
            if dist > (2.5 * atr_1h):
                return "OVERSTRETCHED_MEAN_REVERSION_RISK_DOWN"
            elif dist < -(2.5 * atr_1h):
                return "OVERSTRETCHED_MEAN_REVERSION_RISK_UP"
                
        return "EQUILIBRIUM"

    @staticmethod
    def calc_kinetic_divergence(payload: dict, ltp: float) -> str:
        ema_9_5m = payload.get('ema_9_5m', 0)
        macd_hist_5m = payload.get('macd_hist_5m', 0)
        macd_hist_15m = payload.get('macd_hist_15m', 0)
        
        if ema_9_5m > 0 and ltp > ema_9_5m:
            if macd_hist_5m < 0 and macd_hist_15m < 0:
                return "BEARISH_KINETIC_DIVERGENCE"
        elif ema_9_5m > 0 and ltp < ema_9_5m:
            if macd_hist_5m > 0 and macd_hist_15m > 0:
                return "BULLISH_KINETIC_DIVERGENCE"
                
        return "MOMENTUM_CONFIRMED"

    @classmethod
    def extract_all(cls, payload: dict, ltp: float) -> dict:
        return {
            "fractal_alignment": cls.calc_fractal_alignment(payload),
            "volatility_state": cls.calc_volatility_state(payload, ltp),
            "elasticity_risk": cls.calc_elasticity_risk(payload, ltp),
            "kinetic_divergence": cls.calc_kinetic_divergence(payload, ltp),
            "key_geometry": payload.get("geometry", {})
        }
