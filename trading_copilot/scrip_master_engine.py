import os
import asyncio
import aiohttp
import logging
from datetime import datetime
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache", "UpstoxMaster.csv.gz")
MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/complete.csv.gz"

_scrip_df = None

async def download_scrip_master():
    os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache"), exist_ok=True)
    download_needed = True
    
    if os.path.exists(CACHE_FILE):
        mtime = os.path.getmtime(CACHE_FILE)
        dt_mtime = datetime.fromtimestamp(mtime)
        now = datetime.now()
        
        # Check if downloaded today
        if dt_mtime.date() == now.date():
            download_needed = False

    if download_needed:
        logger.info("Downloading Upstox ScripMaster CSV (Gzipped) via aiohttp...")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(MASTER_URL) as response:
                    if response.status == 200:
                        data = await response.read()
                        with open(CACHE_FILE, 'wb') as f:
                            f.write(data)
                        logger.info("UpstoxMaster.csv.gz downloaded and cached successfully.")
                    else:
                        logger.error(f"Failed to download Upstox ScripMaster: {response.status}")
        except Exception as e:
            logger.error(f"Aiohttp download exception: {e}")
    else:
        logger.info("Using cached UpstoxMaster.csv.gz from today.")
        
    return True

def _get_df():
    global _scrip_df
    if _scrip_df is None:
        if not os.path.exists(CACHE_FILE):
            return pd.DataFrame()
        _scrip_df = pd.read_csv(CACHE_FILE)
        # Drop nan values in critical columns to avoid issues
        _scrip_df = _scrip_df.dropna(subset=['tradingsymbol', 'exchange'])
    return _scrip_df

def _search_scrip_sync(query: str, limit: int):
    df = _get_df()
    if df.empty: return []
    
    query = query.upper()
    
    # Filter for NSE Equities and Indices
    # We want NSE_EQ (EQUITY) and NSE_INDEX (INDEX)
    mask = ((df['exchange'] == 'NSE_EQ') & (df['instrument_type'] == 'EQUITY')) | \
           ((df['exchange'] == 'NSE_INDEX') & (df['instrument_type'] == 'INDEX'))
           
    search_df = df[mask]
    
    # Find exact matches
    exact_mask = (search_df['tradingsymbol'].str.upper() == query) | \
                 (search_df['name'].str.upper() == query)
    exact_matches = search_df[exact_mask].head(limit)
    
    # Find partial matches
    partial_mask = (search_df['tradingsymbol'].str.upper().str.contains(query, na=False)) | \
                   (search_df['name'].str.upper().str.contains(query, na=False))
                   
    # Exclude exact from partial
    if not exact_matches.empty:
        partial_mask = partial_mask & ~search_df.index.isin(exact_matches.index)
        
    partial_matches = search_df[partial_mask].head(limit * 2)
    
    results = pd.concat([exact_matches, partial_matches]).head(limit)
    
    formatted_results = []
    for _, row in results.iterrows():
        # Using exchange_token as the legacy "token" to prevent breaking watchlist format, 
        # but also storing instrument_key
        formatted_results.append({
            "token": str(int(row['exchange_token'])) if pd.notnull(row['exchange_token']) else row['instrument_key'],
            "symbol": row['tradingsymbol'],
            "exchange": "NSE",
            "instrument_key": row['instrument_key']
        })
        
    return formatted_results

async def search_scrip_tokens(query: str, limit: int = 15):
    return await asyncio.to_thread(_search_scrip_sync, query, limit)

def get_instrument_key(symbol: str) -> str:
    """Helper to convert a generic symbol (e.g. 'RELIANCE') to an ISIN (NSE_EQ|INE002A01018)"""
    df = _get_df()
    if df.empty: return f"NSE_EQ|{symbol}"
    
    # Try direct match first for hyphenated symbols like BAJAJ-AUTO
    full_sym = symbol.upper()
    mask = (df['exchange'] == 'NSE_EQ') & (df['tradingsymbol'] == full_sym)
    matches = df[mask]
    if not matches.empty:
        return matches.iloc[0]['instrument_key']
        
    clean_sym = symbol.split('-')[0].upper()
    
    # Check Indices first
    if clean_sym in ['NIFTY', 'NIFTY 50']:
        return 'NSE_INDEX|Nifty 50'
    elif clean_sym in ['BANKNIFTY', 'NIFTY BANK']:
        return 'NSE_INDEX|Nifty Bank'
        
    # Check Equities with cleaned symbol
    mask = (df['exchange'] == 'NSE_EQ') & (df['tradingsymbol'] == clean_sym)
    matches = df[mask]
    if not matches.empty:
        return matches.iloc[0]['instrument_key']
        
    # Check just by token if the symbol passed was actually a token
    try:
        tok_val = float(symbol)
        mask_tok = (df['exchange_token'] == tok_val)
        matches_tok = df[mask_tok]
        if not matches_tok.empty:
            return matches_tok.iloc[0]['instrument_key']
    except ValueError:
        pass
        
    return f"NSE_EQ|{clean_sym}"

def _get_all_fno_equities_sync():
    df = _get_df()
    if df.empty: return {}
    
    # 1. Find all underlying names for NSE_FO OPTSTK
    opt_mask = (df['exchange'] == 'NSE_FO') & (df['instrument_type'] == 'OPTSTK')
    fno_names = set(df[opt_mask]['name'].dropna().unique())
    
    # 2. Find corresponding NSE_EQ tokens
    eq_mask = (df['exchange'] == 'NSE_EQ') & (df['name'].isin(fno_names))
    eq_matches = df[eq_mask]
    
    fno_equities = {}
    for _, row in eq_matches.iterrows():
        token = str(int(row['exchange_token'])) if pd.notnull(row['exchange_token']) else row['instrument_key']
        fno_equities[token] = {
            "symbol": row['tradingsymbol'],
            "exchange": "NSE"
        }
        
    logger.info(f"Dynamically extracted {len(fno_equities)} F&O equities from UpstoxMaster.")
    return fno_equities

async def get_all_fno_equities():
    return await asyncio.to_thread(_get_all_fno_equities_sync)

# Legacy stubs for deprecated Angel One option mapping
async def get_atm_option_tokens(symbol: str, spot_price: float):
    return {}
    
async def get_option_chain_tokens(symbol: str, spot_price: float, num_strikes: int = 10):
    return []
