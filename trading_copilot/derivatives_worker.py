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

# Paths come from the single source of truth. Previously _BASE_DIR resolved to
# AlgoTrade/ so DATA_DIR pointed at a directory that does not exist, and
# get_historical_iv() always returned (999.0, 0.0) -- silently degrading IV Rank
# onto its macro_baselines fallback.
from paths import CACHE_DIR, DATA_DIR, MACRO_BASELINES_PATH, WATCHLIST_PATH, parquet_path
from core.symbols import base_symbol

# Fewest real IV days in the last 252 for an IV rank (scripts/macro_bootstrap MIN_OPTION_DAYS).
IVR_MIN_DAYS = 60


def get_historical_iv(symbol: str):
    """
    Reads the trailing 252 trading days from the 1D Parquet file.
    Returns iv_low, iv_high. If no data, returns (999.0, 0.0)
    """
    file_path = parquet_path(symbol)
    if not file_path.exists():
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
        
        if len(iv_series) < IVR_MIN_DAYS:                       # 3 days (UJJIVANSFB) gave IV rank 100
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
            if MACRO_BASELINES_PATH.exists():
                with open(MACRO_BASELINES_PATH, 'r') as f:
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

ATM_CANDIDATES = 3      # nearest strikes tried on each side for a traded price


def atm_iv_from_quotes(contracts, prices: dict, ltp: float, t: float) -> float:
    """IV in % at the strike nearest the price that has a traded call or put
    (mean of the two when both traded); 0.0 when none of `contracts` traded."""
    by_strike: dict = {}
    for c in contracts:
        p = prices.get(c.instrument_key, 0.0)
        if p > 0:
            by_strike.setdefault(c.strike_price, {})[c.instrument_type] = p
    for k in sorted(by_strike, key=lambda s: abs(s - ltp)):
        ivs = [calc_bsm_iv(p, ltp, k, t, r=0.07, is_call=(kind == 'CE')) for kind, p in by_strike[k].items()]
        ivs = [v for v in ivs if 0.01 < v < 5.0]                # a failed solve is not an IV
        if ivs:
            return round(float(sum(ivs) / len(ivs)) * 100, 2)
    return 0.0


def last_close(symbol: str) -> float:
    """The stored previous close: the price used before the first live tick
    (after a restart, before the open), so IV rank is never blank for want of a tick."""
    try:
        df = pd.read_parquet(parquet_path(symbol), columns=['Close'])
        return float(df['Close'].iloc[-1]) if len(df) else 0.0
    except Exception:
        return 0.0


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
            
        # 3. ATM IV: the nearest strikes that actually traded. Only the single
        # nearest call was tried before; an untraded half-strike (SAIL 177.5,
        # last price 0) left IV -- and so IV rank -- empty.
        atm_iv = 0.0
        if ltp > 0.0:
            try:
                near = [c for c in contracts_res.data if c.expiry == nearest_expiry]
                pick = []
                for kind in ('CE', 'PE'):
                    side = sorted((c for c in near if c.instrument_type == kind),
                                  key=lambda x: abs(x.strike_price - ltp))
                    pick += side[:ATM_CANDIDATES]
                if pick:
                    mq_res = await asyncio.to_thread(mq_api.get_full_market_quote,
                                                     symbol=",".join(c.instrument_key for c in pick), api_version='2.0')
                    prices = {getattr(v, 'instrument_token', None): float(getattr(v, 'last_price', 0.0) or 0.0)
                              for v in (mq_res.data or {}).values()}
                    now = datetime.now(nearest_expiry.tzinfo if nearest_expiry.tzinfo else None)
                    t = max((nearest_expiry - now).total_seconds() / (365.25 * 24 * 3600), 0.001)
                    atm_iv = atm_iv_from_quotes(pick, prices, ltp, t)
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
                if WATCHLIST_PATH.exists():
                    with open(WATCHLIST_PATH, mode='r') as f:
                        active_watchlist = list(csv.DictReader(f))
            except Exception as e:
                logger.error(f"Could not read watchlist.csv: {e}")
                
            tasks = []
            debug_dump = []
            
            # Queue indices
            for idx in index_keys:
                state_key = idx["instrument_key"]
                ltp = TerminalDashboard.active_states.get(state_key, {}).get("ltp") \
                    or (last_close("NIFTY50") if idx["symbol"] == "NIFTY" else 0.0)
                tasks.append((idx["instrument_key"], idx["symbol"], state_key, ltp))
                
            import scrip_master_engine
            # Queue equities
            for row in active_watchlist:
                # E.g. {"Token": "...", "Symbol": "SAIL-EQ", "Exchange": "NSE"}
                exch = row.get('Exchange', row.get('exchange'))
                sym = row.get('Symbol', row.get('symbol'))
                if exch == 'NSE' and sym:
                    clean_sym = base_symbol(sym)
                    ikey = upstox_eq_map.get(clean_sym)
                    if not ikey:
                        ikey = scrip_master_engine.get_instrument_key(clean_sym) or f"NSE_EQ|{clean_sym}"
                    state_key = f"NSE_EQ|{clean_sym}"
                    ltp = TerminalDashboard.active_states.get(state_key, {}).get("ltp") or last_close(clean_sym)
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
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with open(CACHE_DIR / 'derivatives_debug.json', 'w') as f:
                json.dump(debug_dump, f, indent=2)
                
        except Exception as e:
            logger.error(f"Derivatives Poller Loop Error: {e}")
            
        # Wait 2 minutes before next refresh
        await asyncio.sleep(120)
