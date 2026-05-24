import asyncio
import pandas as pd
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class HistoricalFetcher:
    nifty_baseline_df = None

    async def _fetch_single(self, smart_connect_instance, token: str, metadata: dict, interval: str, days_back: int) -> tuple:
        logger.info(f"Fetching warmup data for {metadata['symbol']} ({token})...")
        to_date = datetime.now()
        from_date = to_date - timedelta(days=days_back)
        
        historic_param = {
            "exchange": metadata['exchange'],
            "symboltoken": token,
            "interval": interval,
            "fromdate": from_date.strftime("%Y-%m-%d %H:%M"),
            "todate": to_date.strftime("%Y-%m-%d %H:%M")
        }

        max_retries = 5
        for attempt in range(max_retries):
            try:
                # API call wrapped in to_thread because it's blocking
                response = await asyncio.to_thread(smart_connect_instance.getCandleData, historic_param)
                
                if token == "99926000":
                    print("RAW NIFTY 50 FETCH RESPONSE:", response)
                
                if not response or not response.get("status"):
                    logger.warning(f"Historical API failed for {token}: {response}")
                    candles = []
                else:
                    candles = response.get("data", [])
                    
                df = pd.DataFrame(candles, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                
                if not df.empty:
                    for col in ['open', 'high', 'low', 'close', 'volume']:
                        df[col] = df[col].astype(float)
                    df['timestamp'] = pd.to_datetime(df['timestamp'])
                    
                logger.info(f"Warmup data for {token} fetched: {len(df)} rows.")
                return token, df
                
            except Exception as e:
                err_str = str(e).lower()
                if "access denied" in err_str or "exceeding access rate" in err_str or "couldn't parse" in err_str:
                    if attempt < max_retries - 1:
                        backoff = 15.0 * (attempt + 1)
                        logger.warning(f"Angel One Tarpit detected on {token} (Attempt {attempt+1}/{max_retries}). API cooling down for {backoff}s...")
                        await asyncio.sleep(backoff)
                        continue
                logger.error(f"Error fetching data for {token}: {e}")
                break
                
        return token, pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

    async def fetch_batch_warmups(self, smart_connect_instance, watchlist_dict: dict, interval: str = "FIVE_MINUTE", days_back: int = 5) -> dict:
        logger.info("Starting batch historical fetch (Staggered to respect API rate limits)...")
        
        # Phase 6: Fetch Nifty 50 baseline for Comparative RS
        logger.info("Fetching Nifty 50 Baseline for RS calculations...")
        index_meta = {"exchange": "NSE", "symbol": "Nifty 50"}
        _, index_df = await self._fetch_single(smart_connect_instance, "99926000", index_meta, "ONE_DAY", 100)
        HistoricalFetcher.nifty_baseline_df = index_df
        await asyncio.sleep(4.5)

        results = []
        for token, metadata in watchlist_dict.items():
            # Phase 5.1: Fetch LTF and HTF sequentially
            _, ltf_df = await self._fetch_single(smart_connect_instance, token, metadata, "FIVE_MINUTE", 5)
            await asyncio.sleep(4.5)
            
            _, htf_df = await self._fetch_single(smart_connect_instance, token, metadata, "ONE_DAY", 100) # 100 days for daily to compute RS and long-term EMAs
            await asyncio.sleep(4.5)
            
            results.append((token, {"ltf_df": ltf_df, "htf_df": htf_df}))
        
        # Build multi-tenant cache map with nested structures
        dfs_map = {token: dfs for token, dfs in results}
        return dfs_map
