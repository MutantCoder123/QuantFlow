import os
import csv
import asyncio
import logging
from config import load_watchlist_from_csv
from historical_engine import HistoricalFetcher
from derivatives_engine import OptionsAnalyzer
from technical_engine import MathEngine

logger = logging.getLogger(__name__)

class PreMarketScreener:
    def __init__(self, smart_connect):
        self.smart_connect = smart_connect
        self.master_file = os.path.join(os.path.dirname(__file__), "master_fno_list.csv")
        self.watchlist_file = os.path.join(os.path.dirname(__file__), "watchlist.csv")
        self.fetcher = HistoricalFetcher()

    async def _process_single_stock(self, token, metadata, nifty_df):
        try:
            symbol = metadata.get("symbol", token)
            exchange = metadata.get("exchange", "NSE")
            
            # 1. Fetch Daily Chart
            _, stock_df = await self.fetcher._fetch_single(self.smart_connect, token, metadata, "ONE_DAY", 100)
            if stock_df is None or stock_df.empty:
                logger.debug(f"Failed on {symbol}: Missing historical dataframe.")
                return None
                
            # 2. Calculate Comparative RS
            comp_rs = MathEngine.calc_relative_strength(stock_df, nifty_df)
            
            # 3. Fetch Option Chain
            ivr = 50.0
            if hasattr(self.smart_connect, "getOptionChain"):
                try:
                    params = {"exchange": exchange, "symboltoken": token}
                    response = await asyncio.to_thread(self.smart_connect.getOptionChain, params)
                    if response and response.get("status"):
                        metrics = OptionsAnalyzer.calculate_chain_metrics(response)
                        current_iv = metrics.get("atm_iv", 0.0)
                        
                        state = OptionsAnalyzer.stock_derivatives_state.get(token, {})
                        iv_high = state.get("iv_high", current_iv)
                        iv_low = state.get("iv_low", current_iv)
                        
                        if iv_high != iv_low:
                            ivr = ((current_iv - iv_low) / (iv_high - iv_low)) * 100
                        else:
                            ivr = 0.0
                except Exception as e:
                    logger.warning(f"Option Chain fetch failed for {token}: {e}")
                
            return {
                "token": token,
                "symbol": symbol,
                "exchange": exchange,
                "rs": comp_rs,
                "ivr": ivr
            }
        except Exception as e:
            logger.error(f"Failed on {metadata.get('symbol', token)}: [{type(e).__name__}] {e}")
            return None

    async def run_scan(self):
        logger.info("Initiating Phase 0 Pre-Market Screener...")
        
        # Module 1: File Check
        if not os.path.exists(self.master_file):
            logger.critical(f"Missing master file: {self.master_file}")
            return []
            
        # Fetch Nifty 50 Baseline
        if HistoricalFetcher.nifty_baseline_df is None:
            index_meta = {"exchange": "NSE", "symbol": "Nifty 50"}
            _, index_df = await self.fetcher._fetch_single(self.smart_connect, "99926000", index_meta, "ONE_DAY", 100)
            HistoricalFetcher.nifty_baseline_df = index_df
            
        master_list = load_watchlist_from_csv(self.master_file)
        if not master_list:
            logger.error("Master F&O list is empty.")
            return []
            
        tokens = list(master_list.items())
        all_results = []
        
        # Module 3: Paced Concurrency
        chunk_size = 2
        for i in range(0, len(tokens), chunk_size):
            batch = tokens[i:i+chunk_size]
            tasks = [self._process_single_stock(token, meta, HistoricalFetcher.nifty_baseline_df) for token, meta in batch]
            
            # Gather with return_exceptions to isolate task crashes
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    logger.error(f"Batch task exception: {r}")
                elif r is not None:
                    all_results.append(r)
                    
            # CRITICAL: Physical delay to respect API limits (max 3/sec)
            await asyncio.sleep(1.0)
            
        # 4. Filter and Sort (Restored to Institutional Defaults)
        filtered = [r for r in all_results if r["ivr"] < 35 and r["rs"] > 1.5]
                
        # Sort by RS descending
        filtered.sort(key=lambda x: x["rs"], reverse=True)
        top_picks = filtered[:15]
        
        # Module 4: Terminal Feedback
        logger.info(f"Successfully scanned {len(all_results)}/{len(tokens)} stocks. Top picks compiled.")
        
        self._update_watchlist(top_picks)
        return top_picks

    def _update_watchlist(self, top_picks):
        if not top_picks:
            logger.warning("No stocks passed the screener filter. Watchlist unchanged.")
            return
            
        logger.info(f"Overwriting watchlist.csv with top {len(top_picks)} candidates.")
        with open(self.watchlist_file, 'w', newline='') as f:
            writer = csv.writer(f)
            # Ensure headers match the expected format
            writer.writerow(['Token', 'Symbol', 'Exchange'])
            for pick in top_picks:
                writer.writerow([pick['token'], pick['symbol'], pick['exchange']])
