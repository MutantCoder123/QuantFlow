import os
import csv
import asyncio
import logging
from config import load_watchlist_from_csv
from historical_engine import HistoricalFetcher
from derivatives_engine import OptionsAnalyzer
from technical_engine import MathEngine
from scrip_master_engine import get_all_fno_equities

logger = logging.getLogger(__name__)

class PreMarketScreener:
    def __init__(self, smart_connect):
        self.smart_connect = smart_connect
        self.watchlist_file = os.path.join(os.path.dirname(__file__), "watchlist.csv")
        self.fetcher = HistoricalFetcher()

    async def _process_single_stock(self, token, metadata, nifty_df, catalyst_cache):
        try:
            symbol = metadata.get("symbol", token)
            exchange = metadata.get("exchange", "NSE")
            
            # 1. Fetch Daily Chart
            _, stock_df = await self.fetcher._fetch_single(self.smart_connect, token, metadata, "ONE_DAY", 100)
            if stock_df is None or stock_df.empty:
                logger.debug(f"Failed on {symbol}: Missing historical dataframe.")
                return None
                
            # 2. Calculate Math Scoring Engine (Soft Constraints)
            score = 0
            news_summary = "No fresh news."
            
            if len(stock_df) >= 26:
                live_volume = stock_df['volume'].iloc[-1]
                vol_20ma = stock_df['volume'].rolling(20).mean().iloc[-1]
                if vol_20ma > 0:
                    vol_shock = (live_volume / vol_20ma) * 35
                    score += min(vol_shock, 35)
                    
                # Trend Strength (35%): Strong momentum in EITHER direction (Bullish or Bearish)
                price = stock_df['close'].iloc[-1]
                ema21 = stock_df['close'].ewm(span=21, adjust=False).mean().iloc[-1]
                if abs(price - ema21) / ema21 > 0.005:  # 0.5% away from EMA21
                    score += 15

                ema12 = stock_df['close'].ewm(span=12, adjust=False).mean().iloc[-1]
                ema26 = stock_df['close'].ewm(span=26, adjust=False).mean().iloc[-1]
                if abs(ema12 - ema26) / price > 0.002:  # Strong MACD divergence (bullish or bearish)
                    score += 20
                    
            # News Presence (30%)
            if symbol in catalyst_cache:
                score += 30
                news_summary = catalyst_cache[symbol]
                
            comp_rs = MathEngine.calc_relative_strength(stock_df, nifty_df)
            
            # Relative Strength Outlier Multiplier (25%)
            # Reward massive outperformance or massive underperformance vs the Nifty 50
            if abs(comp_rs) >= 5.0:
                score += 25
                
            # 3. Fetch Option Chain
            ivr = 50.0
            spot_price = stock_df['close'].iloc[-1] if not stock_df.empty else 0
            if spot_price > 0:
                try:
                    from scrip_master_engine import get_option_chain_tokens
                    chain_tokens = await get_option_chain_tokens(symbol, spot_price, num_strikes=4)
                    if chain_tokens:
                        nfo_tokens = [str(t['token']) for t in chain_tokens]
                        response = await asyncio.to_thread(self.smart_connect.getMarketData, mode="FULL", exchangeTokens={"NFO": nfo_tokens})
                        
                        if response and response.get("status"):
                            data = response.get('data', {})
                            fetched_data = data.get('fetched', [])
                            metrics = OptionsAnalyzer._calculate_greeks_and_chain(chain_tokens, fetched_data, spot_price)
                            
                            if metrics:
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
                "ivr": ivr,
                "score": score,
                "news": news_summary
            }
        except Exception as e:
            logger.error(f"Failed on {metadata.get('symbol', token)}: [{type(e).__name__}] {e}")
            return None

    async def run_scan(self):
        logger.info("Initiating Phase 0 Pre-Market Screener...")
        
        # Fetch News Catalyst Cache
        catalyst_cache = {}
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("http://127.0.0.1:8003/state", timeout=3) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        catalyst_cache = data.get("catalyst_cache", {})
        except Exception as e:
            logger.warning(f"Could not fetch news catalyst cache: {e}")
        
        # Fetch Nifty 50 Baseline
        if HistoricalFetcher.nifty_baseline_df is None:
            index_meta = {"exchange": "NSE", "symbol": "Nifty 50"}
            _, index_df = await self.fetcher._fetch_single(self.smart_connect, "99926000", index_meta, "ONE_DAY", 100)
            HistoricalFetcher.nifty_baseline_df = index_df
            
        master_list = await get_all_fno_equities()
        if not master_list:
            logger.error("Failed to load F&O master list dynamically.")
            return []
            
        tokens = list(master_list.items())
        all_results = []
        
        # Module 3: Paced Concurrency
        chunk_size = 2
        for i in range(0, len(tokens), chunk_size):
            batch = tokens[i:i+chunk_size]
            tasks = [self._process_single_stock(token, meta, HistoricalFetcher.nifty_baseline_df, catalyst_cache) for token, meta in batch]
            
            # Gather with return_exceptions to isolate task crashes
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    logger.error(f"Batch task exception: {r}")
                elif r is not None:
                    all_results.append(r)
                    
            # CRITICAL: Physical delay to respect API limits (max 3/sec)
            await asyncio.sleep(1.0)
            
        # 4. Filter and Sort (Phase 14.8 Math Engine)
        filtered = [r for r in all_results if r is not None]
                
        # Sort by mathematical score descending
        filtered.sort(key=lambda x: x["score"], reverse=True)
        top_picks = filtered[:20]
        
        # Module 4: Terminal Feedback
        logger.info(f"Successfully scanned {len(all_results)}/{len(tokens)} stocks. Top picks compiled.")
        
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
