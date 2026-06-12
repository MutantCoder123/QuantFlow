import asyncio
import aiohttp
import sys
import json
import pandas as pd

sys.path.append('trading_copilot/scripts')
import master_bootstrap
import scrip_master_engine

async def verify_nmdc():
    with open('trading_copilot/upstox_token.json', 'r') as f:
        token = json.load(f)['access_token']
        
    headers = {
        'Accept': 'application/json',
        'Authorization': f'Bearer {token}'
    }
    
    rate_limiter = master_bootstrap.RateLimiter()
    
    async with aiohttp.ClientSession() as session:
        print("[1] Fetching last 10 trading days...")
        trading_days = await master_bootstrap.get_trading_days(session, headers, rate_limiter, days=10)
        
        symbol = 'INFY'
        ikey = scrip_master_engine.get_instrument_key(symbol)
        
        print(f"\n[2] Processing {symbol} ({ikey}) for 250 days...")
        updates = await master_bootstrap.process_symbol_completely(session, headers, rate_limiter, ikey, symbol, trading_days)
        
        print(f"\n[3] Calculation complete. Displaying last 10 day updates:")
        df_updates = pd.DataFrame(updates)
        if not df_updates.empty:
            print(df_updates.tail(10).to_string(index=False))
            nan_count = df_updates['EOD_IV'].isna().sum()
            valid_count = len(df_updates) - nan_count
            print(f"\n[STATS] Total Days: {len(df_updates)} | Valid IV: {valid_count} | Blank (NaN): {nan_count}")
        else:
            print("No updates generated.")
            
        print("\n[4] Updating parquet file...")
        await asyncio.to_thread(master_bootstrap.update_parquet, symbol, updates)
        
        # Verify Parquet
        print(f"\n[5] Verifying {symbol}_1D.parquet final status:")
        df_parquet = pd.read_parquet(f'trading_copilot/data/{symbol}_1D.parquet')
        date_col = 'Date' if 'Date' in df_parquet.columns else 'timestamp'
        
        cols_to_print = [date_col, 'Close', 'EOD_IV', 'EOD_PCR', 'Total_OI', 'EOD_MAX_PAIN']
        # Handle lowercase columns
        cols_to_print = [c if c in df_parquet.columns else c.lower() for c in cols_to_print]
        
        print(df_parquet[cols_to_print].tail(10).to_string())

if __name__ == "__main__":
    asyncio.run(verify_nmdc())
