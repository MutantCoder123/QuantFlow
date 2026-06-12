import asyncio
import logging
import os
import pandas as pd
from datetime import datetime
from upstox_client.api.options_api import OptionsApi
from upstox_client.api.market_api import MarketApi
from upstox_client.api.market_quote_api import MarketQuoteApi
from diagnostic_ui import TerminalDashboard
import math
from scipy.stats import norm

logger = logging.getLogger(__name__)

# Base directory for data
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_BASE_DIR, 'data')

def get_historical_iv(symbol: str):
    """
    Reads the trailing 252 trading days from the 1D Parquet file.
    Returns iv_low, iv_high. If no data, returns (999.0, 0.0)
    """
    file_path = os.path.join(DATA_DIR, f"{symbol}_1D.parquet")
    if not os.path.exists(file_path):
        return 999.0, 0.0
        
    try:
        df = pd.read_parquet(file_path, engine='pyarrow')
        if 'EOD_IV' not in df.columns:
            return 999.0, 0.0
            
        # Get trailing 252 trading days
        df = df.sort_values('Date').tail(252)
        if df.empty:
            return 999.0, 0.0
        
        # Filter out 0.0 or NaN IVs
        iv_series = df['EOD_IV'].dropna()
        iv_series = iv_series[iv_series > 0.0]
        
        if iv_series.empty:
            return 999.0, 0.0
            
        return float(iv_series.min()), float(iv_series.max())
    except Exception as e:
        logger.error(f"Error reading Parquet IV for {symbol}: {e}")
        return 999.0, 0.0

def calculate_ivr(symbol: str, live_iv: float):
    """
    Calculates IV Rank given live ATM IV and Parquet history.
    """
    if live_iv <= 0:
        return None
        
    iv_low, iv_high = get_historical_iv(symbol)
    
    if iv_high <= iv_low:
        # Fallback to macro baselines
        import json
        try:
            baselines_path = os.path.join(os.path.dirname(__file__), 'data', 'macro_baselines.json')
            if os.path.exists(baselines_path):
                with open(baselines_path, 'r') as f:
                    data = json.load(f)
                    stock_macro = data.get(symbol, {})
                    vol = stock_macro.get("volatility_edge_52w", {})
                    iv_high = vol.get("iv_52w_high")
                    if iv_high is None: iv_high = 0.0
                    iv_low = vol.get("iv_52w_low")
                    if iv_low is None: iv_low = 999.0
        except Exception as e:
            pass
            
    if iv_high <= iv_low:
        return None
        
    ivr = ((live_iv - iv_low) / (iv_high - iv_low)) * 100
    
    # Cap between 0 and 100 in case live_iv breaks bounds
    return max(0.0, min(100.0, ivr))

def calc_bsm_iv(price, S, K, t, r=0.07, is_call=True):
    if t <= 0 or price <= 0:
        return 0.0
    sigma = 0.3
    for i in range(100):
        try:
            d1 = (math.log(S / K) + (r + sigma**2 / 2) * t) / (sigma * math.sqrt(t))
            d2 = d1 - sigma * math.sqrt(t)
            if is_call:
                diff = S * norm.cdf(d1) - K * math.exp(-r * t) * norm.cdf(d2) - price
                vega = S * norm.pdf(d1) * math.sqrt(t)
            else:
                diff = K * math.exp(-r * t) * norm.cdf(-d2) - S * norm.cdf(-d1) - price
                vega = S * norm.pdf(d1) * math.sqrt(t)
            if abs(diff) < 1e-4:
                return sigma
            if vega == 0:
                break
            sigma -= diff / vega
        except:
            break
    return max(sigma, 0.0)

async def fetch_chain_and_calculate(opt_api, market_api, mq_api, instrument_key: str, symbol: str, ltp: float):
    """
    Fetches the nearest option chain and calculates PCR, Max Pain, and ATM IV.
    """
    try:
        # We still need the nearest expiry to pass to the endpoints
        contracts_res = await asyncio.to_thread(opt_api.get_option_contracts, instrument_key=instrument_key)
        if not contracts_res.data:
            return None
            
        expiries = sorted(list(set(c.expiry for c in contracts_res.data)))
        if not expiries:
            return None
            
        nearest_expiry = expiries[0]
        exp_str = nearest_expiry.strftime('%Y-%m-%d')
        
        from upstox_client.api.market_api import MarketApi
        market_api = MarketApi(opt_api.api_client)
        
        # Current Date (Upstox requires _date parameter, usually current date)
        from datetime import datetime
        today_str = datetime.now().strftime('%Y-%m-%d')
        
        # 1. Fetch PCR
        pcr = None
        try:
            pcr_res = await asyncio.to_thread(market_api.get_pcr_data, instrument_key=instrument_key, expiry=exp_str, _date=today_str, bucket_interval='1')
            if pcr_res and getattr(pcr_res, 'data', None):
                # The data might be a single value or a list. If list, take last.
                if isinstance(pcr_res.data, list) and len(pcr_res.data) > 0:
                    pcr = pcr_res.data[-1].get('pcr', None)
                elif isinstance(pcr_res.data, dict):
                    pcr = pcr_res.data.get('pcr', None)
        except Exception as e:
            logger.error(f"Failed to fetch PCR for {symbol}: {e}")
            
        # 2. Fetch Max Pain
        max_pain_strike = None
        try:
            pain_res = await asyncio.to_thread(market_api.get_max_pain_data, instrument_key=instrument_key, expiry=exp_str, _date=today_str, bucket_interval='1')
            if pain_res and getattr(pain_res, 'data', None):
                if isinstance(pain_res.data, list) and len(pain_res.data) > 0:
                    max_pain_strike = pain_res.data[-1].get('max_pain', None)
                elif isinstance(pain_res.data, dict):
                    max_pain_strike = pain_res.data.get('max_pain', None)
        except Exception as e:
            logger.error(f"Failed to fetch Max Pain for {symbol}: {e}")
            
        # 3. Sniper Fetch ATM IV
        atm_iv = 0.0
        if ltp > 0.0:
            try:
                # Find the ATM contract
                calls = [c for c in contracts_res.data if c.expiry == nearest_expiry and c.instrument_type == 'CE']
                if calls:
                    atm_contract = min(calls, key=lambda x: abs(x.strike_price - ltp))
                    
                    # Fetch market quote for this exact option
                    mq_res = await asyncio.to_thread(mq_api.get_full_market_quote, symbol=atm_contract.instrument_key, api_version='2.0')
                    if mq_res.data and len(mq_res.data) > 0:
                        mq_data = list(mq_res.data.values())[0]
                    else:
                        mq_data = None
                        
                    if mq_data:
                        price = getattr(mq_data, 'last_price', 0.0)
                        if price > 0:
                            # Time to expiry in years
                            now = datetime.now(nearest_expiry.tzinfo if nearest_expiry.tzinfo else None)
                            t = max((nearest_expiry - now).total_seconds() / (365.25 * 24 * 3600), 0.001)
                            
                            atm_iv = calc_bsm_iv(price, ltp, atm_contract.strike_price, t, r=0.07, is_call=True)
                            atm_iv = round(atm_iv * 100, 2)  # Convert to percentage
            except Exception as e:
                logger.error(f"Error fetching/calculating ATM IV for {symbol}: {e}")
        
        ivr_val = calculate_ivr(symbol, atm_iv) if atm_iv > 0 else None
        
        return {
            "pcr": round(float(pcr), 4) if pcr else None,
            "max_pain": float(max_pain_strike) if max_pain_strike else None,
            "atm_iv": atm_iv,
            "ivr": ivr_val
        }

    except Exception as e:
        logger.error(f"Error fetching Option Chain for {symbol}: {e}")
        return None

async def derivatives_poller_loop(api_client, watchlist_tokens: list, upstox_eq_map: dict):
    """
    Background worker that polls Upstox REST API for Option Chains and calculates derived metrics.
    """
    logger.info("Starting Derivatives Option Chain REST Poller...")
    import upstox_client
    opt_api = upstox_client.OptionsApi(api_client)
    
    market_api = upstox_client.MarketApi(api_client)
    mq_api = upstox_client.MarketQuoteApi(api_client)
    
    # We will poll NIFTY and BANKNIFTY explicitly since they might not be in watchlist equity list
    index_keys = [
        {"instrument_key": "NSE_INDEX|Nifty 50", "symbol": "NIFTY", "token": "99926000"},
        {"instrument_key": "NSE_INDEX|Nifty Bank", "symbol": "BANKNIFTY", "token": "99926009"}
    ]
    
    while True:
        try:
            logger.info("Derivatives Worker checking metrics...")
            # We want to pull active watchlist to ensure we calculate for live equities too
            active_watchlist = []
            try:
                import csv
                watchlist_file = os.path.join(_BASE_DIR, 'trading_copilot', 'watchlist.csv')
                if os.path.exists(watchlist_file):
                    with open(watchlist_file, mode='r') as f:
                        active_watchlist = list(csv.DictReader(f))
            except Exception as e:
                logger.error(f"Could not read watchlist.csv: {e}")
                
            tasks = []
            debug_dump = []
            
            # Queue indices
            for idx in index_keys:
                state_key = idx["instrument_key"]
                ltp = TerminalDashboard.active_states.get(state_key, {}).get("ltp", 0.0)
                tasks.append((idx["instrument_key"], idx["symbol"], state_key, ltp))
                
            import scrip_master_engine
            # Queue equities
            for row in active_watchlist:
                # E.g. {"Token": "...", "Symbol": "SAIL-EQ", "Exchange": "NSE"}
                exch = row.get('Exchange', row.get('exchange'))
                sym = row.get('Symbol', row.get('symbol'))
                if exch == 'NSE' and sym:
                    clean_sym = sym.split('-')[0]
                    ikey = upstox_eq_map.get(clean_sym)
                    if not ikey:
                        ikey = scrip_master_engine.get_instrument_key(clean_sym) or f"NSE_EQ|{clean_sym}"
                    state_key = f"NSE_EQ|{clean_sym}"
                    ltp = TerminalDashboard.active_states.get(state_key, {}).get("ltp", 0.0)
                    tasks.append((ikey, clean_sym, state_key, ltp))
                    
            for ikey, clean_sym, state_key, ltp in tasks:
                metrics = await fetch_chain_and_calculate(opt_api, market_api, mq_api, ikey, clean_sym, ltp)
                
                if metrics:
                    logger.debug(f"[DERIVATIVES] {clean_sym} -> PCR: {metrics['pcr']} | Max Pain: {metrics['max_pain']} | IV: {metrics['atm_iv']}%")
                    # Mutate live state gracefully using state key
                    state = TerminalDashboard.active_states.get(state_key)
                    if not state:
                        state = {}
                        TerminalDashboard.active_states[state_key] = state
                        
                    state["stock_pcr"] = metrics["pcr"]
                    state["max_pain_price"] = metrics["max_pain"]
                    state["atm_iv"] = metrics["atm_iv"]
                    if metrics["ivr"] is not None:
                        state["ivr"] = metrics["ivr"]
                        
                    debug_dump.append({
                        "symbol": clean_sym,
                        "state_key": state_key,
                        "metrics": metrics
                    })
                
                # Sleep a tiny bit to avoid rapid burst rate limits
                await asyncio.sleep(1.0)
                
            import json
            os.makedirs('scratch', exist_ok=True)
            with open('scratch/derivatives_debug.json', 'w') as f:
                json.dump(debug_dump, f, indent=2)
                
        except Exception as e:
            logger.error(f"Derivatives Poller Loop Error: {e}")
            
        # Wait 2 minutes before next refresh
        await asyncio.sleep(120)
