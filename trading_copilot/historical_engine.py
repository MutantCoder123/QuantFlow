import asyncio
import pandas as pd
import logging
from core.symbols import base_symbol
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

def fill_recent_days(htf_df, symbol: str):
    """Append the completed sessions Upstox's daily history has not published yet
    (on 2026-10-07 at 08:48 it stopped at 10-05) from the local store
    (data/<SYM>_1D.parquet, filled after each close from the same-day endpoint).
    Without them prev_close was two sessions old and ADV/EMAs a day behind."""
    try:
        from paths import parquet_path
        p = parquet_path(symbol)
        if htf_df is None or htf_df.empty or 'timestamp' not in htf_df or not p.exists():
            return htf_df
        local = pd.read_parquet(p)
        dcol = 'Date' if 'Date' in local.columns else 'timestamp'
        local = local.rename(columns={dcol: 'timestamp', 'Open': 'open', 'High': 'high', 'Low': 'low',
                                      'Close': 'close', 'Volume': 'volume'})
        local['timestamp'] = pd.to_datetime(local['timestamp']).dt.tz_localize(None).dt.normalize()
        ts = pd.to_datetime(htf_df['timestamp'])
        if ts.dt.tz is not None:
            ts = ts.dt.tz_convert('Asia/Kolkata').dt.tz_localize(None)
        last = ts.max().normalize()
        today = pd.Timestamp.now(tz='Asia/Kolkata').tz_localize(None).normalize()
        add = local[(local['timestamp'] > last) & (local['timestamp'] < today)]
        if add.empty:
            return htf_df
        cols = [c for c in ('timestamp', 'open', 'high', 'low', 'close', 'volume') if c in add.columns]
        add = add[cols].copy()
        if ts.dt.tz is None and pd.to_datetime(htf_df['timestamp']).dt.tz is not None:
            add['timestamp'] = add['timestamp'].dt.tz_localize('Asia/Kolkata')
        if 'oi' in htf_df.columns:
            add['oi'] = 0
        out = pd.concat([htf_df, add], ignore_index=True)
        logger.info(f"{symbol}: daily history filled from the local store up to {add['timestamp'].max():%Y-%m-%d}")
        return out
    except Exception as e:
        logger.warning(f"{symbol}: could not fill recent daily candles from the store: {e}")
        return htf_df


class HistoricalFetcher:
    nifty_baseline_df = None

    async def _fetch_single(self, upstox_client, token: str, metadata: dict, interval: str, days_back: int) -> tuple:
        symbol = metadata.get('Symbol', metadata.get('symbol', 'UNKNOWN'))
        
        # Ensure token is an instrument_key for Upstox API
        if token.isdigit() and symbol != 'UNKNOWN':
            import scrip_master_engine
            # Try full symbol first
            ikey = HistoricalFetcher.upstox_eq_map.get(symbol) or scrip_master_engine.get_instrument_key(symbol)
            if not ikey or "NSE_EQ|" + symbol == ikey:
                # If full symbol failed or returned a naive fallback, try splitting (for options backwards compat)
                clean_sym = symbol.split('-')[0]
                ikey_clean = HistoricalFetcher.upstox_eq_map.get(clean_sym) or scrip_master_engine.get_instrument_key(clean_sym)
                if ikey_clean and "NSE_EQ|" + clean_sym != ikey_clean:
                    ikey = ikey_clean
            if ikey:
                token = ikey

        logger.info(f"Fetching warmup data for {symbol} ({token})...")
        to_date = datetime.now()
        from_date = to_date - timedelta(days=days_back)
        
        # Upstox API wants YYYY-MM-DD format
        to_date_str = to_date.strftime("%Y-%m-%d")
        from_date_str = from_date.strftime("%Y-%m-%d")
        
        # Upstox intervals: "1minute", "30minute", "day", "week", "month"
        # Since Upstox doesn't natively expose "5minute", we fetch "1minute" and resample.
        upstox_interval = "1minute" if interval == "FIVE_MINUTE" else "day"

        max_retries = 5
        for attempt in range(max_retries):
            try:
                from upstox_client.api.history_api import HistoryApi
                history_api = HistoryApi(upstox_client)
                
                # Fetch history
                response = await asyncio.to_thread(
                    history_api.get_historical_candle_data1,
                    instrument_key=token,
                    interval=upstox_interval,
                    to_date=to_date_str,
                    from_date=from_date_str,
                    api_version="2.0"
                )
                
                candles = []
                if hasattr(response, 'data') and response.data:
                    candles = response.data.candles
                    
                # Upstox candle format: [timestamp, open, high, low, close, volume, oi]
                if candles:
                    df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume', 'oi'])
                    for col in ['open', 'high', 'low', 'close', 'volume', 'oi']:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                    df['timestamp'] = pd.to_datetime(df['timestamp']).dt.tz_convert('Asia/Kolkata').dt.tz_localize(None)
                    df = df.sort_values('timestamp').reset_index(drop=True)
                    
                    if interval == "FIVE_MINUTE":
                        df = df.set_index('timestamp')
                        df = df.resample('5min').agg({
                            'open': 'first',
                            'high': 'max',
                            'low': 'min',
                            'close': 'last',
                            'volume': 'sum',
                            'oi': 'last'
                        }).dropna().reset_index()
                        
                    logger.info(f"Warmup data for {token} fetched: {len(df)} rows.")
                    return token, df
                else:
                    return token, pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                
            except Exception as e:
                err_str = str(e).lower()
                logger.error(f"EXCEPTION FETCHING {token}: {repr(e)}")
                if "rate" in err_str or "limit" in err_str or "429" in err_str or "access" in err_str:
                    if attempt < max_retries - 1:
                        backoff = 5.0 * (attempt + 1)
                        logger.warning(f"Upstox Tarpit detected on {token} (Attempt {attempt+1}/{max_retries}). API cooling down for {backoff}s...")
                        await asyncio.sleep(backoff)
                        continue
                logger.error(f"Error fetching data for {token}: {e}")
                break
                
        return token, pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

    upstox_eq_map = {}
    upstox_fo_map = {}

    async def fetch_batch_warmups(self, upstox_client, watchlist_dict: dict, interval: str = "FIVE_MINUTE", days_back: int = 5) -> dict:
        logger.info("Starting batch historical fetch (Staggered to respect API rate limits)...")
        
        # Phase 6: Fetch Nifty 50 baseline for Comparative RS
        logger.info("Fetching Nifty 50 Baseline for RS calculations...")
        index_meta = {"exchange": "NSE", "symbol": "Nifty 50"}
        _, index_df = await self._fetch_single(upstox_client, "NSE_INDEX|Nifty 50", index_meta, "ONE_DAY", 100)
        HistoricalFetcher.nifty_baseline_df = index_df
        await asyncio.sleep(1.0)

        logger.info("Downloading Upstox Instrument Master...")
        import scrip_master_engine
        await scrip_master_engine.download_scrip_master()
        
        HistoricalFetcher.upstox_eq_map = {}
        HistoricalFetcher.upstox_fo_map = {}
        for row in watchlist_dict.values():
            sym = row.get('Symbol', row.get('symbol', ''))
            clean_sym = base_symbol(sym)
            if clean_sym:
                HistoricalFetcher.upstox_eq_map[clean_sym] = scrip_master_engine.get_instrument_key(clean_sym)

        results = []
        for token, metadata in watchlist_dict.items():
            sym = metadata.get('Symbol', metadata.get('symbol', ''))
            clean_sym = base_symbol(sym)
            exch = metadata.get('Exchange', metadata.get('exchange', ''))
            
            if exch == 'NFO':
                ikey = HistoricalFetcher.upstox_fo_map.get(clean_sym, f"NSE_FO|{clean_sym}")
            else:
                ikey = HistoricalFetcher.upstox_eq_map.get(clean_sym, scrip_master_engine.get_instrument_key(clean_sym))
                
            # Keep original NSE_EQ|SAIL string as reference token for the Mutator to match Websocket
            ws_token = f"NSE_FO|{clean_sym}" if exch == "NFO" else f"NSE_EQ|{clean_sym}"
                
            # Phase 5.1: Fetch LTF and HTF sequentially
            # Fetch 30 days of intraday data to provide enough bars for 4h indicators (EMA-21 requires at least 21 bars = 84 hours = ~14 trading days)
            _, ltf_df = await self._fetch_single(upstox_client, ikey, metadata, "FIVE_MINUTE", 30)
            await asyncio.sleep(1.0)
            
            _, htf_df = await self._fetch_single(upstox_client, ikey, metadata, "ONE_DAY", 100)
            await asyncio.sleep(1.0)
            
            # Save results under ws_token so Mutator can look them up by ws_token!
            htf_df = fill_recent_days(htf_df, clean_sym)
            results.append((ws_token, {"ltf_df": ltf_df, "htf_df": htf_df}))
        
        # Build multi-tenant cache map with nested structures
        dfs_map = {token: dfs for token, dfs in results}
        return dfs_map

    async def fetch_and_save_parquet_batch(self, smart_connect_instance, watchlist_dict: dict, days_back: int = 1500):
        logger.info(f"Fetching Parquet History for new tokens: {list(watchlist_dict.keys())}")
        from pathlib import Path
        import asyncio
        import os
        _BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
        DATA_DIR = _BASE_DIR / 'data'
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        for token, metadata in watchlist_dict.items():
            symbol = base_symbol(metadata.get('symbol', metadata.get('Symbol', '')))
            exch = metadata.get('exchange', metadata.get('Exchange', ''))
            if not symbol: continue
            
            if exch == 'NFO':
                ikey = HistoricalFetcher.upstox_fo_map.get(symbol, f"NSE_FO|{symbol}")
            else:
                import scrip_master_engine
                ikey = HistoricalFetcher.upstox_eq_map.get(symbol, scrip_master_engine.get_instrument_key(symbol))
            
            logger.info(f"Downloading historical parquet for {symbol} ({ikey})...")
            _, df = await self._fetch_single(smart_connect_instance, ikey, metadata, "ONE_DAY", days_back)
            if not df.empty:
                file_path = DATA_DIR / f"{symbol}_1D.parquet"
                df.to_parquet(file_path, engine='pyarrow')
                logger.info(f"Saved {symbol}_1D.parquet ({len(df)} rows)")
            await asyncio.sleep(4.5)
