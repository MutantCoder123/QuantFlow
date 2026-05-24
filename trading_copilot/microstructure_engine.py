class MicrostructureEngine:
    cvd_state = {}
    vol_profile_state = {}

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
    def generate_microstructure_payload(cls, tick_dict) -> dict:
        bids = tick_dict.get("bids", [])
        asks = tick_dict.get("asks", [])
        token = tick_dict.get('token')
        ltp = tick_dict.get('price', 0.0)
        vol = tick_dict.get('volume', 0.0)
        
        obi = cls.calc_obi(bids, asks)
        
        best_bid_price = bids[0].get('price', 0) if bids else 0
        best_ask_price = asks[0].get('price', float('inf')) if asks else float('inf')
        
        cvd = cls.update_cvd(
            token, 
            ltp, 
            vol, 
            best_bid_price, 
            best_ask_price
        )
        
        # Update Spatial Liquidity (POC)
        cls.update_volume_profile(token, ltp, vol)
        poc = cls.calculate_poc(token, ltp)
        
        poc_distance_pct = 0.0
        if poc > 0:
            poc_distance_pct = ((ltp - poc) / poc) * 100
        
        return {
            "obi": round(obi, 4),
            "cvd": cvd,
            "poc_price": poc,
            "poc_distance_pct": round(poc_distance_pct, 4)
        }
