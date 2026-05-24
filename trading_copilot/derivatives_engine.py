import asyncio
import logging
import numpy as np
import traceback
from datetime import datetime
import scrip_master_engine
import scipy.stats as si

logger = logging.getLogger(__name__)

def implied_volatility(target_value, S, K, T, r, option_type, max_iterations=100, precision=1.0e-5):
    """
    Newton-Raphson Black-Scholes IV Solver
    target_value: Option LTP
    S: Spot Price
    K: Strike Price
    T: Time to Expiry (Years)
    r: Risk-free rate
    option_type: 'c' for Call, 'p' for Put
    """
    if T <= 0.0 or target_value <= 0.0 or S <= 0.0 or K <= 0.0:
        return 0.0
        
    sigma = 0.5 # Initial guess
    for i in range(0, max_iterations):
        d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)
        
        if option_type == 'c':
            price = (S * si.norm.cdf(d1, 0.0, 1.0) - K * np.exp(-r * T) * si.norm.cdf(d2, 0.0, 1.0))
        else:
            price = (K * np.exp(-r * T) * si.norm.cdf(-d2, 0.0, 1.0) - S * si.norm.cdf(-d1, 0.0, 1.0))
            
        vega = S * si.norm.pdf(d1, 0.0, 1.0) * np.sqrt(T)
        
        diff = target_value - price
        
        if abs(diff) < precision:
            return sigma
            
        if vega == 0.0:
            return 0.0 # Prevent division by zero
            
        sigma = sigma + diff / vega # f(x) / f'(x)
        
        # Prevent negative volatility or absurdly high volatility
        if sigma <= 0.0:
            sigma = 0.001
        elif sigma > 5.0:
            return 5.0
            
    return sigma

logger = logging.getLogger(__name__)

class OptionsAnalyzer:
    macro_state = {
        "pcr": 1.0, 
        "max_call_oi_strike": 0, 
        "max_put_oi_strike": 0
    }
    
    stock_derivatives_state = {}

    @staticmethod
    def _calculate_greeks_and_chain(chain_tokens, market_data, spot_price):
        if not market_data or not chain_tokens:
            return None
            
        # Map tokens to their market data
        md_map = {str(item.get("symbolToken")): item for item in market_data if item}
        
        ce_ois = []
        pe_ois = []
        strikes = []
        ce_ivs = []
        pe_ivs = []
        
        max_ce_oi = -1
        max_pe_oi = -1
        max_ce_strike = 0
        max_pe_strike = 0
        total_ce_oi = 0
        total_pe_oi = 0
        
        # Calculate time to expiry
        try:
            exp_str = chain_tokens[0]['expiry']
            exp_date = datetime.strptime(exp_str, "%d%b%Y")
            days_to_expiry = max((exp_date - datetime.now()).days, 0)
            t_years = max(days_to_expiry / 365.0, 0.001)
        except Exception:
            t_years = 0.01
            
        r = 0.10 # 10% risk free rate estimate for India

        # Group by strike
        strikes_map = {}
        for token_obj in chain_tokens:
            strike = float(token_obj['strike']) / 100.0
            if strike not in strikes_map:
                strikes_map[strike] = {'CE': None, 'PE': None}
            
            symbol = token_obj['symbol']
            t_id = str(token_obj['token'])
            
            md = md_map.get(t_id, {})
            oi = float(md.get("opnInterest", 0))
            ltp = float(md.get("ltp", 0))
            
            if symbol.endswith('CE'):
                strikes_map[strike]['CE'] = {'oi': oi, 'ltp': ltp}
            elif symbol.endswith('PE'):
                strikes_map[strike]['PE'] = {'oi': oi, 'ltp': ltp}

        # Process strikes
        for strike in sorted(strikes_map.keys()):
            ce_data = strikes_map[strike]['CE'] or {'oi': 0, 'ltp': 0}
            pe_data = strikes_map[strike]['PE'] or {'oi': 0, 'ltp': 0}
            
            c_oi = ce_data['oi']
            p_oi = pe_data['oi']
            
            strikes.append(strike)
            ce_ois.append(c_oi)
            pe_ois.append(p_oi)
            
            total_ce_oi += c_oi
            total_pe_oi += p_oi
            
            if c_oi > max_ce_oi:
                max_ce_oi = c_oi
                max_ce_strike = strike
            if p_oi > max_pe_oi:
                max_pe_oi = p_oi
                max_pe_strike = strike
                
            # Calculate IV
            c_iv = 0.0
            p_iv = 0.0
            if implied_volatility and spot_price > 0:
                try:
                    if ce_data['ltp'] > 0:
                        c_iv = implied_volatility(ce_data['ltp'], spot_price, strike, t_years, r, 'c')
                    if pe_data['ltp'] > 0:
                        p_iv = implied_volatility(pe_data['ltp'], spot_price, strike, t_years, r, 'p')
                except:
                    pass
            ce_ivs.append(c_iv)
            pe_ivs.append(p_iv)

        pcr = 1.0
        if total_ce_oi > 0:
            pcr = total_pe_oi / total_ce_oi

        # Max Pain Calculation
        max_pain = 0.0
        atm_iv = 0.0
        if strikes:
            S = np.array(strikes)
            C = np.array(ce_ois)
            P = np.array(pe_ois)
            
            pain_values = []
            for X in S:
                call_liability = np.sum(C * np.maximum(0, X - S))
                put_liability = np.sum(P * np.maximum(0, S - X))
                pain_values.append(call_liability + put_liability)
            
            min_pain_idx = np.argmin(pain_values)
            max_pain = S[min_pain_idx]
            
            # Extract ATM IV using Max Pain strike index
            ce_iv_atm = ce_ivs[min_pain_idx]
            pe_iv_atm = pe_ivs[min_pain_idx]
            atm_iv = (ce_iv_atm + pe_iv_atm) / 2.0
            if atm_iv == 0:
                atm_iv = ce_iv_atm or pe_iv_atm

        return {
            "pcr": round(pcr, 4),
            "resistance_wall": max_ce_strike,
            "support_wall": max_pe_strike,
            "max_pain": float(max_pain),
            "atm_iv": float(atm_iv)
        }

    async def _fetch_and_process_token(self, smart_connect, symbol: str, token: str, is_index: bool = False):
        try:
            # Local import to prevent circular dependency
            from diagnostic_ui import TerminalDashboard
            
            # Grab spot price
            state = TerminalDashboard.active_states.get(token, {})
            spot_price = state.get("ltp", 0.0)
            
            # If LTP is 0, we can't reliably map the ATM strikes. Fallback to basic.
            if spot_price <= 0:
                logger.debug(f"LTP for {symbol} is 0. Skipping options chain fetch.")
                return

            # Get full chain tokens (10 strikes = 20 CE/PE pairs = 40 tokens)
            chain_tokens = await scrip_master_engine.get_option_chain_tokens(symbol, spot_price, num_strikes=10)
            
            if not chain_tokens:
                logger.debug(f"No valid option tokens found for {symbol} near {spot_price}")
                return
                
            logger.info(f"Fetching Option Chain data for {symbol} via SmartAPI FULL mode...")
                
            nfo_tokens = [str(t['token']) for t in chain_tokens]
            
            # Fetch OI via SmartAPI getMarketData
            response = await asyncio.to_thread(smart_connect.getMarketData, "FULL", {"NFO": nfo_tokens})
            
            if response and response.get('status'):
                data = response.get('data', {})
                fetched_data = data.get('fetched', [])
                
                metrics = self._calculate_greeks_and_chain(chain_tokens, fetched_data, spot_price)
                
                if metrics:
                    if is_index:
                        OptionsAnalyzer.macro_state["pcr"] = metrics["pcr"]
                        OptionsAnalyzer.macro_state["max_call_oi_strike"] = metrics["resistance_wall"]
                        OptionsAnalyzer.macro_state["max_put_oi_strike"] = metrics["support_wall"]
                        logger.info(f"[DERIVATIVES MACRO] {symbol} PCR: {metrics['pcr']} | Resistance: {metrics['resistance_wall']} | Support: {metrics['support_wall']}")
                    else:
                        # IV Rolling State Tracking
                        prev_state = OptionsAnalyzer.stock_derivatives_state.get(token, {})
                        iv_high = prev_state.get("iv_high", 0.0)
                        iv_low = prev_state.get("iv_low", 999.0)
                        
                        current_iv = metrics["atm_iv"]
                        if current_iv > 0:
                            iv_high = max(iv_high, current_iv)
                            if iv_low == 999.0:
                                iv_low = current_iv
                            else:
                                iv_low = min(iv_low, current_iv)
                        
                        ivr = 0.0
                        if iv_high > iv_low:
                            ivr = ((current_iv - iv_low) / (iv_high - iv_low)) * 100

                        OptionsAnalyzer.stock_derivatives_state[token] = {
                            "stock_pcr": metrics["pcr"],
                            "max_pain_price": metrics["max_pain"],
                            "current_iv": round(current_iv, 4),
                            "iv_high": round(iv_high, 4),
                            "iv_low": round(iv_low, 4),
                            "ivr": round(ivr, 2)
                        }
            else:
                logger.error(f"Failed to fetch market data for {symbol}. Response: {response}")

        except Exception as e:
            logger.warning(f"Derivatives API fetch failed for {symbol} (Likely timeout/rate-limit): {e}")

    async def start_polling(self, smart_connect_instance, watchlist: dict, index_token="99926000"):
        logger.info("Initializing Angel One Micro-Derivatives Polling Engine (Playwright Removed)...")
        
        while True:
            try:
                tasks = []
                # Add Index task (Macro State)
                tasks.append(self._fetch_and_process_token(smart_connect_instance, "NIFTY", index_token, is_index=True))
                
                # Add individual stock tasks (Micro State)
                for token, meta in watchlist.items():
                    if meta.get('is_option'):
                        continue # Skip the NFO tokens injected into the watchlist
                        
                    raw_symbol = meta.get('symbol', '')
                    clean_symbol = raw_symbol.split('-')[0].upper()
                    tasks.append(self._fetch_and_process_token(smart_connect_instance, clean_symbol, token, is_index=False))
                
                # Run API fetches sequentially with delay to respect rate limits
                for task in tasks:
                    await task
                    await asyncio.sleep(2.0)
                    
            except Exception as e:
                logger.error(f"Derivatives Polling Engine loop error: {e}")
            
            # Sleep for 3 minutes before refreshing the OI chain
            await asyncio.sleep(180)
