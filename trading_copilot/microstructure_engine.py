import time
import datetime
from collections import deque
import numpy as np

class MicrostructureEngine:
    cvd_state = {}
    vol_profile_state = {}
    
    # Institutional Order Flow States
    session_vwap_state = {}
    whale_cvd_state = {}
    whale_cvd_history = {}

    @classmethod
    def update_volume_profile(cls, token, ltp, volume):
        if token not in cls.vol_profile_state:
            cls.vol_profile_state[token] = {}
            
        # Round LTP to nearest integer to avoid float bloat and create finite bins
        price_bin = int(round(ltp))
        cls.vol_profile_state[token][price_bin] = cls.vol_profile_state[token].get(price_bin, 0) + volume

    @classmethod
    def calculate_poc(cls, token, current_ltp):
        profile = cls.vol_profile_state.get(token, {})
        if not profile:
            return int(round(current_ltp))
            
        # Return the price bin key that has the maximum volume
        return max(profile, key=profile.get)

    @staticmethod
    def calc_obi(bids, asks) -> float:
        if not bids or not asks:
            return 0.0
            
        total_bids = sum(b.get('quantity', 0) for b in bids)
        total_asks = sum(a.get('quantity', 0) for a in asks)
        
        if total_bids + total_asks == 0:
            return 0.0
            
        return (total_bids - total_asks) / (total_bids + total_asks)

    @classmethod
    def update_cvd(cls, token, ltp, volume, best_bid_price, best_ask_price) -> int:
        if token not in cls.cvd_state:
            cls.cvd_state[token] = 0
            
        if ltp >= best_ask_price:
            cls.cvd_state[token] += int(volume)
        elif ltp <= best_bid_price:
            cls.cvd_state[token] -= int(volume)
            
        return cls.cvd_state[token]

    @classmethod
    def update_session_vwap(cls, token, ltp, volume) -> float:
        now = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
        current_date_str = now.strftime('%Y-%m-%d')
        
        if token not in cls.session_vwap_state:
            cls.session_vwap_state[token] = {'cumulative_pv': 0.0, 'cumulative_v': 0.0, 'last_reset_date': current_date_str}
            
        state = cls.session_vwap_state[token]
        
        # Reset at 09:15 IST each day
        if state['last_reset_date'] != current_date_str and now.hour >= 9 and now.minute >= 15:
            state['cumulative_pv'] = 0.0
            state['cumulative_v'] = 0.0
            state['last_reset_date'] = current_date_str
            
        state['cumulative_pv'] += float(ltp * volume)
        state['cumulative_v'] += float(volume)
        
        if state['cumulative_v'] == 0:
            return ltp
        return round(state['cumulative_pv'] / state['cumulative_v'], 2)

    @classmethod
    def update_whale_cvd(cls, token, ltp, volume, best_bid_price, best_ask_price) -> int:
        if token not in cls.whale_cvd_state:
            cls.whale_cvd_state[token] = 0
            
        transaction_value = float(ltp * volume)
        if transaction_value > 500000:
            if ltp >= best_ask_price:
                cls.whale_cvd_state[token] += int(volume)
            elif ltp <= best_bid_price:
                cls.whale_cvd_state[token] -= int(volume)
                
        return cls.whale_cvd_state[token]

    @classmethod
    def calculate_whale_ema_and_slope(cls, token, current_whale_cvd) -> tuple:
        if token not in cls.whale_cvd_history:
            cls.whale_cvd_history[token] = deque(maxlen=60)
            
        history = cls.whale_cvd_history[token]
        now_ts = time.time()
        
        # Sample every 60 seconds
        if len(history) == 0 or (now_ts - history[-1][0]) >= 60:
            history.append((now_ts, current_whale_cvd))
            
        ema_1h = 0.0
        if len(history) > 0:
            alpha = 2.0 / (60 + 1)
            ema_1h = history[0][1]
            for t, val in list(history)[1:]:
                ema_1h = (val - ema_1h) * alpha + ema_1h
        else:
            ema_1h = current_whale_cvd
            
        slope = 0.0
        if len(history) >= 2:
            cutoff_ts = now_ts - (15 * 60)
            valid_points = [x for x in history if x[0] >= cutoff_ts]
            if len(valid_points) >= 2:
                y = [x[1] for x in valid_points]
                x = list(range(len(y)))
                if len(y) > 1:
                    slope = float(np.polyfit(x, y, 1)[0])
                    
        return round(ema_1h, 2), round(slope, 4)

    @classmethod
    def generate_microstructure_payload(cls, tick_dict) -> dict:
        bids = tick_dict.get("bids", [])
        asks = tick_dict.get("asks", [])
        token = tick_dict.get('token')
        ltp = tick_dict.get('price', 0.0)
        vol = tick_dict.get('volume', 0.0)
        if not hasattr(cls, 'last_vtt_state'):
            cls.last_vtt_state = {}
            
        prev_vol = cls.last_vtt_state.get(token, vol)
        tick_vol = max(0, vol - prev_vol)
        cls.last_vtt_state[token] = vol
        
        obi = cls.calc_obi(bids, asks)
        
        best_bid_price = bids[0].get('price', 0) if bids else 0
        best_ask_price = asks[0].get('price', float('inf')) if asks else float('inf')
        
        if not hasattr(cls, 'last_bba_state'):
            cls.last_bba_state = {}
            
        if token not in cls.last_bba_state:
            cls.last_bba_state[token] = {'bid': 0.0, 'ask': float('inf')}
            
        if best_bid_price > 0:
            cls.last_bba_state[token]['bid'] = best_bid_price
        else:
            best_bid_price = cls.last_bba_state[token]['bid']
            
        if best_ask_price != float('inf'):
            cls.last_bba_state[token]['ask'] = best_ask_price
        else:
            best_ask_price = cls.last_bba_state[token]['ask']
        
        cvd = cls.update_cvd(
            token, 
            ltp, 
            tick_vol, 
            best_bid_price, 
            best_ask_price
        )
        
        session_vwap = cls.update_session_vwap(token, ltp, tick_vol)
        whale_cvd = cls.update_whale_cvd(token, ltp, tick_vol, best_bid_price, best_ask_price)
        whale_ema, whale_slope = cls.calculate_whale_ema_and_slope(token, whale_cvd)
        
        # Update Spatial Liquidity (POC)
        cls.update_volume_profile(token, ltp, tick_vol)
        poc = cls.calculate_poc(token, ltp)
        
        poc_distance_pct = 0.0
        if poc > 0:
            poc_distance_pct = ((ltp - poc) / poc) * 100
        
        price_to_vwap_pct = 0.0
        if session_vwap > 0:
            price_to_vwap_pct = ((ltp - session_vwap) / session_vwap) * 100
        
        return {
            "obi": round(obi, 4),
            "cvd": cvd,
            "poc_price": poc,
            "poc_distance_pct": round(poc_distance_pct, 4),
            "session_vwap": session_vwap,
            "price_to_vwap_pct": round(price_to_vwap_pct, 4),
            "whale_cvd_live": whale_cvd,
            "whale_cvd_ema_1h": whale_ema,
            "whale_cvd_slope": whale_slope
        }
