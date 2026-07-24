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
                
            # 2. Calculate Math Scoring Engine (V2 - Directionally Aware)
            magnitude_score = 0
            net_polarity = 0.0  # Positive = Bullish, Negative = Bearish
            news_summary = "No fresh news."
            
            if len(stock_df) >= 26:
                live_volume = stock_df['volume'].iloc[-1]
                vol_20ma = stock_df['volume'].rolling(20).mean().iloc[-1]
                if vol_20ma > 0:
                    vol_shock = (live_volume / vol_20ma) * 35
                    magnitude_score += min(vol_shock, 35)
                    
                # Trend Strength (35%)
                price = stock_df['close'].iloc[-1]
                ema21 = stock_df['close'].ewm(span=21, adjust=False).mean().iloc[-1]
                trend_dist = (price - ema21) / ema21
                if abs(trend_dist) > 0.005:  # 0.5% away from EMA21
                    magnitude_score += 15
                    net_polarity += trend_dist * 100

                ema12 = stock_df['close'].ewm(span=12, adjust=False).mean().iloc[-1]
                ema26 = stock_df['close'].ewm(span=26, adjust=False).mean().iloc[-1]
                macd_dist = (ema12 - ema26) / price
                if abs(macd_dist) > 0.002:  # Strong MACD divergence
                    magnitude_score += 20
                    net_polarity += macd_dist * 1000
                    
            # 3. News Sentiment Scoring (30%)
            if symbol in catalyst_cache:
                news_data = catalyst_cache[symbol]
                
                if isinstance(news_data, dict):
                    news_summary = news_data.get('summary', 'Fresh news available.')
                    sentiment = news_data.get('sentiment', 'NEUTRAL')
                    impact = news_data.get('impact', 'LOW')
                    
                    impact_multiplier = {'HIGH': 30, 'MEDIUM': 15, 'LOW': 5}.get(impact, 5)
                    magnitude_score += impact_multiplier
                    
                    if sentiment == 'POSITIVE':
                        net_polarity += impact_multiplier
                    elif sentiment == 'NEGATIVE':
                        net_polarity -= impact_multiplier
                else:
                    # Fallback for plain string cache
                    news_summary = str(news_data)
                    magnitude_score += 15
                
            # 4. Relative Strength Outlier Multiplier (25%)
            comp_rs = MathEngine.calc_relative_strength(stock_df, nifty_df)
            if abs(comp_rs) >= 5.0:
                magnitude_score += 25
                net_polarity += comp_rs
                
            directional_bias = "LONG" if net_polarity > 0 else "SHORT"
            
            # 5. Extract Structural Levels
            prev_day_high = round(float(stock_df['high'].iloc[-1]), 2) if not stock_df.empty else 0.0
            prev_day_low = round(float(stock_df['low'].iloc[-1]), 2) if not stock_df.empty else 0.0
            camarilla = MathEngine.calc_camarilla_pivots(stock_df)
                
            return {
                "token": token,
                "symbol": symbol,
                "exchange": exchange,
                "rs": comp_rs,
                "score": magnitude_score,
                "directional_bias": directional_bias,
                "news": news_summary,
                "prev_day_high": prev_day_high,
                "prev_day_low": prev_day_low,
                "camarilla": camarilla
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
