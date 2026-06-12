import asyncio
import logging
import os
import json
import time
from datetime import datetime
import pandas as pd

import upstox_client

logger = logging.getLogger(__name__)

# Base directory for data
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_BASE_DIR, 'data')
os.makedirs(DATA_DIR, exist_ok=True)

def _get_api_clients():
    token_file = os.path.join(_BASE_DIR, "upstox_token.json")
    try:
        with open(token_file, "r") as f:
            data = json.load(f)
            access_token = data.get("access_token")
        if not access_token:
            return None, None
            
        configuration = upstox_client.Configuration()
        configuration.access_token = access_token
        api_client = upstox_client.ApiClient(configuration)
        
        hist_api = upstox_client.HistoryApi(api_client)
        opt_api = upstox_client.OptionsApi(api_client)
        return hist_api, opt_api
    except Exception as e:
        logger.error(f"Failed to read upstox_token.json: {e}")
        return None, None

def _calculate_option_metrics(opt_data: list):
    """Calculates ATM IV, EOD PCR, and EOD Max Pain from option chain data."""
    if not opt_data:
        return 0.0, 0.0, 0.0
        
    total_ce_oi = 0
    total_pe_oi = 0
    strikes_ce = {}
    strikes_pe = {}
    atm_iv = 0.0
    
    # Very rudimentary Max Pain & PCR calculation
    # For a real implementation, you'd find the strike with min intrinsic value loss
    for item in opt_data:
        strike = item.strike_price
        if item.call_options:
            ce_oi = item.call_options.market_data.oi if item.call_options.market_data else 0
            total_ce_oi += ce_oi
            strikes_ce[strike] = ce_oi
            # Grab IV (rough approximation if ATM isn't explicitly defined)
            if item.call_options.option_greeks and item.call_options.option_greeks.iv:
                atm_iv = item.call_options.option_greeks.iv # Will just use the last one for now if no spot
                
        if item.put_options:
            pe_oi = item.put_options.market_data.oi if item.put_options.market_data else 0
            total_pe_oi += pe_oi
            strikes_pe[strike] = pe_oi

    pcr = (total_pe_oi / total_ce_oi) if total_ce_oi > 0 else 0.0
    
    # Calculate Max Pain
    max_pain_strike = 0.0
    min_pain_val = float('inf')
    all_strikes = set(strikes_ce.keys()).union(set(strikes_pe.keys()))
    
    for test_strike in all_strikes:
        pain = 0
        for ce_strike, oi in strikes_ce.items():
            if ce_strike < test_strike:
                pain += (test_strike - ce_strike) * oi
        for pe_strike, oi in strikes_pe.items():
            if pe_strike > test_strike:
                pain += (pe_strike - test_strike) * oi
                
        if pain < min_pain_val:
            min_pain_val = pain
            max_pain_strike = test_strike

    return atm_iv, pcr, max_pain_strike

def _sync_symbol_sync(symbol: str, hist_api, opt_api):
    """Synchronous file I/O and API calls for a single symbol."""
    try:
        import scrip_master_engine
        ikey = scrip_master_engine.get_instrument_key(symbol)
            
        # 1. Fetch OHLCV
        to_date = datetime.now().strftime("%Y-%m-%d")
        file_path = os.path.join(DATA_DIR, f"{symbol}_1D.parquet")
        
        # If file doesn't exist (new symbol), fetch 1500 days of history for IVR. Else, just last 5 days.
        days_back = 1500 if not os.path.exists(file_path) else 5
        from_date = (datetime.now() - pd.Timedelta(days=days_back)).strftime("%Y-%m-%d")
        
        hist_res = hist_api.get_historical_candle_data1(
            instrument_key=ikey,
            interval="day",
            to_date=to_date,
            from_date=from_date,
            api_version="2.0"
        )
        
        if not hist_res.data or not hist_res.data.candles:
            logger.warning(f"No OHLCV data found for {symbol}")
            return False
            
        # Process all fetched candles (could be 1500 for a new symbol)
        rows = []
        for candle in hist_res.data.candles:
            rows.append({
                "Date": candle[0][:10],
                "Open": candle[1],
                "High": candle[2],
                "Low": candle[3],
                "Close": candle[4],
                "Volume": candle[5],
                "EOD_IV": 0.0,
                "EOD_PCR": 0.0,
                "EOD_MAX_PAIN": 0.0
            })
            
        new_df = pd.DataFrame(rows)
        new_df['Date'] = pd.to_datetime(new_df['Date'])
        new_df = new_df.sort_values('Date').reset_index(drop=True)
        
        # 2. Fetch Option Metrics for the LATEST day
        atm_iv, pcr, max_pain = 0.0, 0.0, 0.0
        try:
            contracts_res = opt_api.get_option_contracts(instrument_key=ikey)
            if contracts_res.data:
                expiries = sorted(list(set(c.expiry for c in contracts_res.data)))
                if expiries:
                    opt_res = opt_api.get_put_call_option_chain(
                        instrument_key=ikey,
                        expiry_date=expiries[0]
                    )
                    if opt_res.data:
                        atm_iv, pcr, max_pain = _calculate_option_metrics(opt_res.data)
        except Exception as e:
            logger.error(f"Failed to fetch option metrics for {symbol}: {e}")
            
        # Update the latest day's row with option metrics
        if not new_df.empty:
            new_df.at[new_df.index[-1], 'EOD_IV'] = atm_iv
            new_df.at[new_df.index[-1], 'EOD_PCR'] = pcr
            new_df.at[new_df.index[-1], 'EOD_MAX_PAIN'] = max_pain
        
        if os.path.exists(file_path):
            df = pd.read_parquet(file_path, engine='pyarrow')
            # The existing file from historical fetcher might use 'timestamp' instead of 'Date'
            if 'timestamp' in df.columns and 'Date' not in df.columns:
                df.rename(columns={'timestamp': 'Date', 'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'}, inplace=True)
                # Keep EOD metrics if they exist, or set to NaN
            
            # Remove existing row for the same date to avoid duplicates
            if 'Date' in df.columns:
                # Ensure Date is datetime
                df['Date'] = pd.to_datetime(df['Date'])
                df = df[df['Date'].dt.date != new_df['Date'].iloc[0].date()]
            df = pd.concat([df, new_df], ignore_index=True)
        else:
            df = new_df
            
        df.to_parquet(file_path, engine='pyarrow')
        logger.info(f"Successfully synced {symbol} EOD Parquet.")
        return True
    except Exception as e:
        logger.error(f"Error syncing {symbol}: {e}")
        return False

async def sync_eod_parquet(symbols: list):
    """
    Main async function to trigger the manual database synchronization pipeline.
    Offloads synchronous API requests and disk I/O to a background thread.
    """
    logger.info(f"Starting EOD Parquet sync for {len(symbols)} symbols...")
    hist_api, opt_api = _get_api_clients()
    if not hist_api:
        logger.error("Sync failed: Upstox API unauthenticated.")
        return False
        
    for sym in symbols:
        # Strip suffix if any (e.g. SAIL-EQ -> SAIL)
        clean_sym = sym.split('-')[0]
        await asyncio.to_thread(_sync_symbol_sync, clean_sym, hist_api, opt_api)
        
    logger.info("EOD Parquet sync completed.")
    return True
