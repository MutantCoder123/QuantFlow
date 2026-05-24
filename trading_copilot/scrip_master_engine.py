import os
import json
import asyncio
import aiohttp
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

CACHE_FILE = "cache/ScripMaster.json"
MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"

_scrip_cache = None
option_map = {}

async def download_scrip_master():
    os.makedirs("cache", exist_ok=True)
    download_needed = True
    
    if os.path.exists(CACHE_FILE):
        mtime = os.path.getmtime(CACHE_FILE)
        dt_mtime = datetime.fromtimestamp(mtime)
        now = datetime.now()
        
        # Check if downloaded today
        if dt_mtime.date() == now.date():
            download_needed = False

    if download_needed:
        logger.info("Downloading ScripMaster.json (50MB) via aiohttp...")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(MASTER_URL) as response:
                    if response.status == 200:
                        data = await response.text()
                        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                            f.write(data)
                        logger.info("ScripMaster.json downloaded and cached successfully.")
                    else:
                        logger.error(f"Failed to download ScripMaster: {response.status}")
        except Exception as e:
            logger.error(f"Aiohttp download exception: {e}")
    else:
        logger.info("Using cached ScripMaster.json from today.")
        
    return True

def _get_atm_sync(symbol: str, spot_price: float):
    global _scrip_cache
    if _scrip_cache is None:
        if not os.path.exists(CACHE_FILE):
            logger.error("ScripMaster cache missing!")
            return {}
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            _scrip_cache = json.load(f)
            
    # Filter for options
    options = [
        d for d in _scrip_cache 
        if d.get('name') == symbol and d.get('instrumenttype') in ['OPTSTK', 'OPTIDX']
    ]
    
    if not options:
        return {}
        
    # Find closest upcoming expiry
    now = datetime.now()
    valid_expiries = set()
    for o in options:
        try:
            exp_date = datetime.strptime(o['expiry'], "%d%b%Y")
            if exp_date >= now:
                valid_expiries.add(exp_date)
        except:
            pass
            
    if not valid_expiries:
        return {}
        
    closest_expiry = min(valid_expiries)
    closest_expiry_str = closest_expiry.strftime("%d%b%Y").upper()
    
    # Filter for this exact expiry
    options_near = [o for o in options if o['expiry'].upper() == closest_expiry_str]
    
    ce_tokens = [o for o in options_near if o['symbol'].endswith('CE')]
    pe_tokens = [o for o in options_near if o['symbol'].endswith('PE')]
    
    if not ce_tokens or not pe_tokens:
        return {}
        
    # Find ATM strike
    # Strike in Angel One is * 100, so we divide by 100
    closest_ce = min(ce_tokens, key=lambda x: abs(float(x['strike'])/100.0 - spot_price))
    closest_pe = min(pe_tokens, key=lambda x: abs(float(x['strike'])/100.0 - spot_price))
    
    ce_tok = str(closest_ce['token'])
    pe_tok = str(closest_pe['token'])
    ce_strike = float(closest_ce['strike'])/100.0
    pe_strike = float(closest_pe['strike'])/100.0
    
    option_map[ce_tok] = {"parent": symbol, "type": "CE", "strike": ce_strike}
    option_map[pe_tok] = {"parent": symbol, "type": "PE", "strike": pe_strike}
    
    return {
        "CE": ce_tok,
        "PE": pe_tok
    }

def _get_chain_sync(symbol: str, spot_price: float, num_strikes: int = 10):
    global _scrip_cache
    if _scrip_cache is None:
        if not os.path.exists(CACHE_FILE):
            return []
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            _scrip_cache = json.load(f)
            
    options = [
        d for d in _scrip_cache 
        if d.get('name') == symbol and d.get('instrumenttype') in ['OPTSTK', 'OPTIDX']
    ]
    if not options: return []
        
    now = datetime.now()
    valid_expiries = set()
    for o in options:
        try:
            exp_date = datetime.strptime(o['expiry'], "%d%b%Y")
            if exp_date >= now:
                valid_expiries.add(exp_date)
        except: pass
            
    if not valid_expiries: return []
        
    closest_expiry = min(valid_expiries)
    closest_expiry_str = closest_expiry.strftime("%d%b%Y").upper()
    
    options_near = [o for o in options if o['expiry'].upper() == closest_expiry_str]
    
    # Extract unique strikes
    strikes = list(set([float(o['strike'])/100.0 for o in options_near]))
    strikes.sort()
    
    # Find closest strike index
    if not strikes: return []
    closest_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot_price))
    
    start_idx = max(0, closest_idx - num_strikes)
    end_idx = min(len(strikes), closest_idx + num_strikes + 1)
    target_strikes = strikes[start_idx:end_idx]
    
    # Collect tokens
    tokens = []
    for o in options_near:
        if float(o['strike'])/100.0 in target_strikes:
            tokens.append(o)
            
    return tokens

async def get_atm_option_tokens(symbol: str, spot_price: float):
    logger.info(f"Mapping ATM tokens for {symbol} at {spot_price}...")
    return await asyncio.to_thread(_get_atm_sync, symbol, spot_price)

async def get_option_chain_tokens(symbol: str, spot_price: float, num_strikes: int = 10):
    return await asyncio.to_thread(_get_chain_sync, symbol, spot_price, num_strikes)

def _search_scrip_sync(query: str, limit: int):
    global _scrip_cache
    if _scrip_cache is None:
        if not os.path.exists(CACHE_FILE):
            return []
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            _scrip_cache = json.load(f)
            
    query = query.upper()
    results = []
    for scrip in _scrip_cache:
        # Match symbol or name
        if query in scrip.get('symbol', '').upper() or query in scrip.get('name', '').upper():
            if scrip.get('exch_seg') in ['NSE', 'BSE']:
                results.append({
                    "token": str(scrip['token']),
                    "symbol": scrip['symbol'],
                    "exchange": scrip['exch_seg']
                })
                if len(results) >= limit:
                    break
    return results

async def search_scrip_tokens(query: str, limit: int = 15):
    return await asyncio.to_thread(_search_scrip_sync, query, limit)

