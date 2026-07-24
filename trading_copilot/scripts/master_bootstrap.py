import os
import sys
import json
import asyncio
import aiohttp
import pandas as pd
import numpy as np
from datetime import datetime
import urllib.parse
from tqdm.asyncio import tqdm

# Add parent directory to path
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE_DIR not in sys.path:
    sys.path.append(_BASE_DIR)

import scrip_master_engine
from derivatives_engine import implied_volatility

class RateLimiter:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.force_sleep = False
        
    async def trigger_hard_sleep(self):
        async with self.lock:
            self.force_sleep = True

    async def _countdown_sleep(self, seconds: int, reason: str):
        print(f"\n[RATE LIMIT] {reason}. Sleeping for {seconds} seconds...")
        for _ in tqdm(range(seconds), desc="Cooldown", unit="s", leave=True):
            await asyncio.sleep(1)

    async def wait_if_needed(self):
        async with self.lock:
            if self.force_sleep:
                await self._countdown_sleep(1805, "Upstox Rate Limit (429) Triggered")
                self.force_sleep = False
                return

            # Strictly serialize API launches to ~8.3 req/sec to prevent 429s
            await asyncio.sleep(0.12)

async def fetch_with_rate_limit(session, url, headers, rate_limiter, params=None, retries=3):
    for attempt in range(retries):
        await rate_limiter.wait_if_needed()
        try:
            async with session.get(url, headers=headers, params=params, timeout=10) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status == 429:
                    # Hit external limit (likely because main.py ate some API quota).
                    await rate_limiter.trigger_hard_sleep()
                    continue # Loop will retry after wait_if_needed executes the 30-min sleep
                elif response.status == 401:
                    print("\n[ERROR] Upstox Token Expired. Please restart the main server to renew the token.")
                    os._exit(1)
                else:
                    text = await response.text()
                    print(f"\n[API ERROR] {url} returned {response.status}: {text}")
                    return None
        except Exception as e:
            if attempt == retries - 1:
                print(f"\n[NETWORK ERROR] Failed to fetch {url}: {e}")
                return None
            await asyncio.sleep(2)

async def get_trading_days(session, headers, rate_limiter, days=260):
    """Fetch the last `days` trading days using NIFTY 50 historical candles."""
    nifty_ikey = "NSE_INDEX|Nifty 50"
    nifty_ikey_enc = urllib.parse.quote(nifty_ikey)
    to_date = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - pd.Timedelta(days=days + 150)).strftime("%Y-%m-%d")
    
    url = f"https://api.upstox.com/v2/historical-candle/{nifty_ikey_enc}/day/{to_date}/{from_date}"
    data = await fetch_with_rate_limit(session, url, headers, rate_limiter)
    
    if not data or not data.get('data', {}).get('candles'):
        print("[ERROR] Failed to fetch trading days via NIFTY 50 candles.")
        return []
        
    # Extract dates from candles, sort descending
    candles = data['data']['candles']
    dates = sorted([c[0][:10] for c in candles], reverse=True)
    return dates[:days]

async def fetch_contracts_for_expiry(session, headers, rate_limiter, ikey, expiry):
    url = "https://api.upstox.com/v2/expired-instruments/option/contract"
    data = await fetch_with_rate_limit(session, url, headers, rate_limiter, params={'instrument_key': ikey, 'expiry_date': expiry})
    if data and data.get('data'):
        return data['data']
    return []

async def fetch_candles_for_contract(session, headers, rate_limiter, sem, contract_ikey, to_date, from_date):
    async with sem:
        enc_ikey = urllib.parse.quote(contract_ikey)
        is_live = len(contract_ikey.split('|')) == 2
        if is_live:
            url = f"https://api.upstox.com/v2/historical-candle/{enc_ikey}/day/{to_date}/{from_date}"
        else:
            url = f"https://api.upstox.com/v2/expired-instruments/historical-candle/{enc_ikey}/day/{to_date}/{from_date}"
            
        data = await fetch_with_rate_limit(session, url, headers, rate_limiter)
        if data and data.get('data') and data['data'].get('candles'):
            return contract_ikey, data['data']['candles']
        return contract_ikey, []

def update_parquet(symbol, updates):
    """Update the SYMBOL_1D.parquet file with the new metrics."""
    parquet_path = os.path.join(_BASE_DIR, 'data', f"{symbol}_1D.parquet")
    if not os.path.exists(parquet_path):
        return # Skip if file doesn't exist
        
    df = pd.read_parquet(parquet_path)
    
    # 1. Standardize Schema: Drop legacy 'oi' column
    if 'oi' in df.columns:
        df.drop(columns=['oi'], inplace=True)
        
    # 2. Standardize Schema: Rename lowercase columns to capitalized
    rename_map = {
        'timestamp': 'Date',
        'open': 'Open',
        'high': 'High',
        'low': 'Low',
        'close': 'Close',
        'volume': 'Volume'
    }
    df.rename(columns=rename_map, inplace=True)
    
    date_col = 'Date'
    if date_col not in df.columns:
        return # Cannot update if there is no date column
        
    df[date_col] = pd.to_datetime(df[date_col]).dt.strftime('%Y-%m-%d')
    
    # Ensure EOD columns exist before updating
    for col in ['EOD_PCR', 'EOD_MAX_PAIN', 'EOD_IV', 'Total_OI']:
        if col not in df.columns:
            df[col] = np.nan if col == 'EOD_IV' else 0.0
    
    # Apply updates
    for u in updates:
        idx = df[df[date_col] == u['Date']].index
        if not idx.empty:
            df.loc[idx, 'EOD_PCR'] = u['PCR']
            df.loc[idx, 'Total_OI'] = u['Total_OI']
            df.loc[idx, 'EOD_MAX_PAIN'] = u['Max_Pain']
            df.loc[idx, 'EOD_IV'] = u['EOD_IV']
            
    # Save back
    df.to_parquet(parquet_path, index=False)

async def process_symbol_completely(session, headers, rate_limiter, ikey, symbol, trading_days):
    """Deep Backfill via Contract-Centric Method."""
    min_date = min(trading_days)
    max_date = max(trading_days)
    
    # 1. Fetch expiries
    exp_url = "https://api.upstox.com/v2/expired-instruments/expiries"
    exp_data = await fetch_with_rate_limit(session, exp_url, headers, rate_limiter, params={'instrument_key': ikey})
    if not exp_data or not exp_data.get('data'):
        return []
    
    expiries = sorted([exp['expiry_date'] if isinstance(exp, dict) else exp for exp in exp_data['data']])
    # Filter expiries relevant to our 30 day window (allow some buffer) and strictly limit to 2 monthly expiries
    active_expiries = [e for e in expiries if e >= min_date]
    
    # 2. Fetch contracts for all active expiries
    all_contracts = []
    print(f"\n[{symbol}] Fetching contracts for {len(active_expiries)} expiries...")
    for exp in active_expiries:
        contracts = await fetch_contracts_for_expiry(session, headers, rate_limiter, ikey, exp)
        all_contracts.extend(contracts)
        
    # Inject Live Contracts for the current active month
    try:
        import scrip_master_engine
        df_live = scrip_master_engine._get_df()
        if not df_live.empty:
            eq_row = df_live[df_live['instrument_key'] == ikey]
            if not eq_row.empty:
                underlying_name = eq_row.iloc[0]['name']
                mask = (df_live['exchange'] == 'NSE_FO') & (df_live['instrument_type'] == 'OPTSTK') & (df_live['name'] == underlying_name)
                live_opts = df_live[mask]
                if not live_opts.empty:
                    live_expiries = sorted(live_opts['expiry'].unique())
                    if live_expiries:
                        live_exp = live_expiries[0]
                        live_opts = live_opts[live_opts['expiry'] == live_exp]
                        print(f"[{symbol}] Appending {len(live_opts)} live contracts for expiry {live_exp}...")
                        for _, row in live_opts.iterrows():
                            all_contracts.append({
                                'instrument_key': row['instrument_key'],
                                'expiry': row['expiry'],
                                'strike_price': float(row['strike']),
                                'instrument_type': row['option_type']
                            })
    except Exception as e:
        print(f"[{symbol}] Warning: Could not append live contracts: {e}")
        
    if not all_contracts:
        return []
        
    # Map contract keys to their metadata
    contract_meta = {c['instrument_key']: c for c in all_contracts}
    contract_keys = list(contract_meta.keys())
    
    # 3. Fetch candles concurrently
    print(f"[{symbol}] Downloading historical candles for {len(contract_keys)} contracts...")
    sem = asyncio.Semaphore(20) # Concurrency limit for requests
    tasks = [fetch_candles_for_contract(session, headers, rate_limiter, sem, c_ikey, max_date, min_date) for c_ikey in contract_keys]
    
    all_candles = []
    for f in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc=f"[{symbol}] Candles", leave=False):
        c_ikey, candles = await f
        if candles:
            meta = contract_meta[c_ikey]
            for c in candles:
                # [timestamp, open, high, low, close, volume, open_interest]
                all_candles.append({
                    'Date': c[0][:10],
                    'Close': float(c[4]),
                    'OI': float(c[6]) if len(c) > 6 else 0.0,
                    'Type': meta.get('instrument_type'),
                    'Strike': float(meta.get('strike_price', 0)),
                    'Expiry': meta.get('expiry') or meta.get('expiry_date'),
                    'InstrumentKey': c_ikey
                })
                
    if not all_candles:
        return []
        
    df_candles = pd.DataFrame(all_candles)
    
    # 4. Read underlying spot prices
    parquet_path = os.path.join(_BASE_DIR, 'data', f"{symbol}_1D.parquet")
    spot_dict = {}
    if os.path.exists(parquet_path):
        df_spot = pd.read_parquet(parquet_path)
        date_col = 'Date' if 'Date' in df_spot.columns else 'timestamp'
        close_col = 'Close' if 'Close' in df_spot.columns else 'close'
        
        if date_col in df_spot.columns:
            df_spot['DateStr'] = pd.to_datetime(df_spot[date_col]).dt.strftime('%Y-%m-%d')
            spot_dict = df_spot.set_index('DateStr')[close_col].to_dict()

    # 5. Calculate daily metrics
    updates = []
    print(f"[{symbol}] Aggregating locally for {len(trading_days)} days...")
    for date_str in trading_days:
        day_df = df_candles[df_candles['Date'] == date_str]
        if day_df.empty:
            continue
            
        # Filter to active expiry
        # active expiry is smallest expiry >= date_str
        future_expiries = day_df[day_df['Expiry'] >= date_str]['Expiry'].unique()
        if len(future_expiries) == 0:
            continue
        active_expiry = min(future_expiries)
        
        opt_df = day_df[day_df['Expiry'] == active_expiry]
        
        # a) PCR & Total OI
        ce_oi = opt_df[opt_df['Type'] == 'CE']['OI'].sum()
        pe_oi = opt_df[opt_df['Type'] == 'PE']['OI'].sum()
        pcr = pe_oi / ce_oi if ce_oi > 0 else 0.0
        total_oi = ce_oi + pe_oi
        
        # b) Max Pain
        spot_price = spot_dict.get(date_str, 0.0)
        strikes = np.sort(opt_df['Strike'].unique())
        
        max_pain = 0.0
        if len(strikes) > 0 and spot_price > 0:
            min_loss = float('inf')
            best_strike = 0.0
            for test_strike in strikes:
                # Loss = CE buyers win (TestStrike - Strike) + PE buyers win (Strike - TestStrike)
                # Weighted by Open Interest
                ce_loss = np.sum(np.maximum(0, test_strike - opt_df[(opt_df['Type'] == 'CE')]['Strike']) * opt_df[(opt_df['Type'] == 'CE')]['OI'])
                pe_loss = np.sum(np.maximum(0, opt_df[(opt_df['Type'] == 'PE')]['Strike'] - test_strike) * opt_df[(opt_df['Type'] == 'PE')]['OI'])
                total_loss = ce_loss + pe_loss
                if total_loss < min_loss:
                    min_loss = total_loss
                    best_strike = test_strike
            max_pain = best_strike

        # c) EOD IV
        atm_iv = np.nan
        if max_pain > 0 and spot_price > 0:
            # Time to expiry
            exp_dt = datetime.strptime(active_expiry, "%Y-%m-%d")
            cur_dt = datetime.strptime(date_str, "%Y-%m-%d")
            days_to_expiry = max((exp_dt - cur_dt).days, 0)
            t_years = max(days_to_expiry / 365.0, 0.001)
            
            def get_strike_iv(target_strike):
                ce_row = opt_df[(opt_df['Type'] == 'CE') & (opt_df['Strike'] == target_strike)]
                pe_row = opt_df[(opt_df['Type'] == 'PE') & (opt_df['Strike'] == target_strike)]
                ce_prem = ce_row['Close'].iloc[0] if not ce_row.empty else 0.0
                pe_prem = pe_row['Close'].iloc[0] if not pe_row.empty else 0.0
                
                valid = []
                if ce_prem > 0 and pe_prem > 0:
                    i_c = implied_volatility(ce_prem, spot_price, target_strike, t_years, 0.07, 'c')
                    i_p = implied_volatility(pe_prem, spot_price, target_strike, t_years, 0.07, 'p')
                    valid = [i for i in [i_c, i_p] if not np.isnan(i)]
                return float(np.mean(valid)) if valid else np.nan

            # Primary attempt: Max Pain Strike
            atm_iv = get_strike_iv(max_pain)
            
            # Smart Fallback Interpolator
            if np.isnan(atm_iv):
                higher_strikes = strikes[strikes > max_pain]
                lower_strikes = strikes[strikes < max_pain]
                
                fallback_ivs = []
                if len(higher_strikes) > 0:
                    h_iv = get_strike_iv(higher_strikes[0])
                    if not np.isnan(h_iv): fallback_ivs.append(h_iv)
                if len(lower_strikes) > 0:
                    l_iv = get_strike_iv(lower_strikes[-1])
                    if not np.isnan(l_iv): fallback_ivs.append(l_iv)
                    
                if fallback_ivs:
                    atm_iv = float(np.mean(fallback_ivs))
                    
        updates.append({
            'Date': date_str,
            'PCR': round(pcr, 4),
            'Total_OI': float(total_oi),
            'Max_Pain': max_pain,
            'EOD_IV': round(atm_iv, 2) if not np.isnan(atm_iv) else np.nan
        })
        
    return updates

def is_eod_truly_filled(df):
    """Returns boolean mask of rows with genuinely computed EOD data (not defaults)."""
    return (
        (df.get('EOD_IV', pd.Series(dtype=float)).fillna(0) != 0) |
        (df.get('EOD_PCR', pd.Series(dtype=float)).fillna(0) != 0) |
        (df.get('Total_OI', pd.Series(dtype=float)).fillna(0) != 0) |
        (df.get('EOD_MAX_PAIN', pd.Series(dtype=float)).fillna(0) != 0)
    )

def get_missing_trading_days_and_completion(symbol, full_trading_days):
    """Returns (is_completed, eod_missing_days, needs_ohlcv_sync)"""
    parquet_path = os.path.join(_BASE_DIR, 'data', f"{symbol}_1D.parquet")
    if not os.path.exists(parquet_path):
        return False, full_trading_days, True
        
    try:
        df = pd.read_parquet(parquet_path)
        date_col = 'Date' if 'Date' in df.columns else 'timestamp'
        if date_col not in df.columns:
            return False, full_trading_days, True
            
        df['DateStr'] = pd.to_datetime(df[date_col]).dt.strftime('%Y-%m-%d')
        existing_dates = set(df['DateStr'].tolist())
        
        # 1. Check OHLCV freshness
        ohlcv_missing = [d for d in full_trading_days if d not in existing_dates]
        needs_ohlcv_sync = len(ohlcv_missing) > 0
        
        # 2. Check EOD freshness with CORRECTED validity
        if 'EOD_IV' not in df.columns:
            eod_missing = list(full_trading_days)
        else:
            truly_valid_mask = is_eod_truly_filled(df)
            completed_eod_dates = set(df[truly_valid_mask]['DateStr'].tolist())
            eod_missing = [d for d in full_trading_days if d not in completed_eod_dates]
            
        is_completed = len(eod_missing) == 0 and not needs_ohlcv_sync
        return is_completed, eod_missing, needs_ohlcv_sync
    except Exception as e:
        print(f"[{symbol}] Error reading parquet: {e}")
        return False, full_trading_days, True

async def sync_ohlcv_to_today(session, headers, rate_limiter, symbol, ikey, full_trading_days):
    """Fetch missing OHLCV candles and append to the parquet file."""
    parquet_path = os.path.join(_BASE_DIR, 'data', f"{symbol}_1D.parquet")
    
    if os.path.exists(parquet_path):
        df = pd.read_parquet(parquet_path)
        date_col = 'Date' if 'Date' in df.columns else 'timestamp'
        df['DateStr'] = pd.to_datetime(df[date_col]).dt.strftime('%Y-%m-%d')
        existing_dates = set(df['DateStr'].tolist())
    else:
        df = pd.DataFrame()
        existing_dates = set()
        
    missing_dates = [d for d in full_trading_days if d not in existing_dates]
    if not missing_dates:
        return 0
        
    from_date = min(missing_dates)
    to_date = max(missing_dates)
    
    enc_ikey = urllib.parse.quote(ikey)
    url = f"https://api.upstox.com/v2/historical-candle/{enc_ikey}/day/{to_date}/{from_date}"
    data = await fetch_with_rate_limit(session, url, headers, rate_limiter)
    
    if not data or not data.get('data', {}).get('candles'):
        return 0
        
    candles = data['data']['candles']
    new_rows = []
    for c in candles:
        date_str = c[0][:10]
        if date_str not in existing_dates:
            new_rows.append({
                'Date': date_str,
                'Open': float(c[1]),
                'High': float(c[2]),
                'Low': float(c[3]),
                'Close': float(c[4]),
                'Volume': float(c[5]),
                'EOD_PCR': 0.0,
                'EOD_MAX_PAIN': 0.0,
                'EOD_IV': np.nan,
                'Total_OI': 0.0
            })
            
    if new_rows:
        new_df = pd.DataFrame(new_rows)
        rename_map = {'timestamp': 'Date', 'open': 'Open', 'high': 'High', 'low': 'Low', 'close': 'Close', 'volume': 'Volume'}
        df.rename(columns=rename_map, inplace=True, errors='ignore')
        if 'oi' in df.columns:
            df.drop(columns=['oi'], inplace=True)
        if 'DateStr' in df.columns:
            df.drop(columns=['DateStr'], inplace=True)
            
        for col in ['EOD_PCR', 'EOD_MAX_PAIN', 'EOD_IV', 'Total_OI']:
            if col not in df.columns:
                df[col] = np.nan if col == 'EOD_IV' else 0.0
                
        combined = pd.concat([df, new_df], ignore_index=True)
        combined['Date'] = pd.to_datetime(combined['Date'])
        combined = combined.drop_duplicates(subset=['Date']).sort_values('Date').reset_index(drop=True)
        combined.to_parquet(parquet_path, index=False)
        
    return len(new_rows)

async def main(dry_run=False):
    token_path = os.path.join(_BASE_DIR, 'upstox_token.json')
    if not os.path.exists(token_path):
        print("[ERROR] Token missing.")
        sys.exit(1)
        
    with open(token_path, 'r') as f:
        access_token = json.load(f)['access_token']
        
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Accept': 'application/json'
    }
    
    rate_limiter = RateLimiter()
    
    print("Loading F&O Universe...")
    fno_dict = await scrip_master_engine.get_all_fno_equities()
    if not fno_dict:
        print("[ERROR] No F&O data.")
        sys.exit(1)
        
    print("Loading Watchlist...")
    watchlist_path = os.path.join(_BASE_DIR, 'watchlist.csv')
    if not os.path.exists(watchlist_path):
        print("[ERROR] watchlist.csv not found.")
        sys.exit(1)
        
    raw_watchlist = pd.read_csv(watchlist_path)['Symbol'].str.strip().str.upper()
    watchlist = raw_watchlist.apply(lambda x: x.split('-')[0]).tolist()
    
    fno_items = []
    completed_symbols = []
    
    print("Fetching Trading Days...")
    async with aiohttp.ClientSession() as session:
        # Limit to 250 trading days max
        trading_days = await get_trading_days(session, headers, rate_limiter, days=250)
        if not trading_days:
            sys.exit(1)
            
        if dry_run:
            trading_days = trading_days[:2]
            print(f"[DRY RUN] Limited to {len(trading_days)} days: {trading_days}")
            
        for token, meta in fno_dict.items():
            symbol = meta['symbol'].split('-')[0].upper()
            if symbol in watchlist:
                is_completed, eod_missing, needs_ohlcv = get_missing_trading_days_and_completion(symbol, trading_days)
                if is_completed:
                    print(f"Skipping {symbol} (fully synced to {max(trading_days)})")
                    completed_symbols.append(symbol)
                    continue
                
                fno_items.append((token, meta, eod_missing, needs_ohlcv))
                
        print(f"Filtered to {len(fno_items)} F&O stocks matching watchlist requiring updates.")
        if dry_run:
            fno_items = fno_items[:2]
            print(f"[DRY RUN] Limited to {len(fno_items)} symbols.")
            
        # PASS 1: OHLCV Sync (fast)
        print(f"\n--- Pass 1: OHLCV Price Sync ---")
        for token, meta, eod_missing, needs_ohlcv in fno_items:
            if needs_ohlcv:
                symbol = meta['symbol'].split('-')[0].upper()
                ikey = scrip_master_engine.get_instrument_key(symbol)
                count = await sync_ohlcv_to_today(session, headers, rate_limiter, symbol, ikey, trading_days)
                print(f"  [{symbol}] Synced {count} new OHLCV days")
                
        # PASS 2: EOD Derivatives Backfill (slow)
        print(f"\n--- Pass 2: EOD Derivatives Backfill ---")
        for token, meta, eod_missing, needs_ohlcv in tqdm(fno_items, desc="Symbols Processed"):
            if not eod_missing:
                continue
            symbol = meta['symbol'].split('-')[0].upper()
            ikey = scrip_master_engine.get_instrument_key(symbol)
            
            print(f"\n[{symbol}] Processing {len(eod_missing)} missing days for derivatives...")
            updates = await process_symbol_completely(session, headers, rate_limiter, ikey, symbol, eod_missing)
            if updates:
                await asyncio.to_thread(update_parquet, symbol, updates)

    print("\n[SUCCESS] Master Bootstrap Completed.")

if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    asyncio.run(main(dry_run=dry_run))
