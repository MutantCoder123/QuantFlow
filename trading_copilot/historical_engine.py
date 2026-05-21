import asyncio
import pandas as pd
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class HistoricalFetcher:
    async def _fetch_single(self, smart_connect_instance, token: str, metadata: dict, interval: str, days_back: int) -> tuple:
        try:
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
            
            # API call wrapped in to_thread because it's blocking
            response = await asyncio.to_thread(smart_connect_instance.getCandleData, historic_param)
            
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
            logger.error(f"Error fetching data for {token}: {e}")
            return token, pd.DataFrame(columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

    async def fetch_batch_warmups(self, smart_connect_instance, watchlist_dict: dict, interval: str = "FIVE_MINUTE", days_back: int = 5) -> dict:
        logger.info("Starting concurrent batch historical fetch...")
        tasks = []
        for token, metadata in watchlist_dict.items():
            tasks.append(self._fetch_single(smart_connect_instance, token, metadata, interval, days_back))
            
        results = await asyncio.gather(*tasks)
        
        # Build multi-tenant cache map
        dfs_map = {token: df for token, df in results}
        return dfs_map
