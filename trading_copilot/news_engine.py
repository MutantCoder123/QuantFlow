import os
import asyncio
import logging
import time
import json
from datetime import datetime, timedelta
from google import genai

import upstox_client

logger = logging.getLogger(__name__)

class NewsEngine:
    catalyst_cache = {}
    active_loop = None
    current_watchlist = {}
    current_model = "gemini-2.5-flash"
    current_interval = 120
    last_fetch_time = 0
    macro_context = None

    @classmethod
    def _get_upstox_api(cls):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        root_token = os.path.join(os.path.dirname(base_dir), "upstox_token.json")
        copilot_token = os.path.join(base_dir, "upstox_token.json")
        token_file = copilot_token if os.path.exists(copilot_token) else root_token
        
        try:
            with open(token_file, "r") as f:
                data = json.load(f)
                access_token = data.get("access_token")
            if not access_token:
                return None
            configuration = upstox_client.Configuration()
            configuration.access_token = access_token
            api_client = upstox_client.ApiClient(configuration)
            return upstox_client.NewsApi(api_client)
        except Exception as e:
            logger.error(f"Failed to read upstox_token.json: {e}")
            return None

    @classmethod
    async def analyze_catalyst(cls, headline: str, summary: str, model_name: str = "gemini-2.5-flash"):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return "NEUTRAL - Gemini API Key missing for catalyst analysis."
            
        try:
            client = genai.Client(api_key=api_key)
            prompt = (
                f"You are a quantitative text parser. Analyze this news: {headline} - {summary}. "
                "Categorize it and summarize it in exactly one sentence. "
                "Format MUST be exactly: '[BULLISH/BEARISH/NEUTRAL] - [1-sentence summary]'. "
                "If no relevant stock news is present, return 'NEUTRAL - No immediate fundamental catalyst detected.'"
            )
            
            response = await client.aio.models.generate_content(
                model=model_name,
                contents=prompt
            )
            return response.text.strip()
        except Exception as e:
            logger.error(f"Gemini API error during news parsing: {e}")
            return "NEUTRAL - Catalyst parsing failed due to API error."

    @classmethod
    def _fallback_payload(cls, symbol: str):
        return {
            "raw_news": []
        }

    @classmethod
    async def fetch_live_feeds(cls, watchlist: dict, model_name: str = "gemini-2.5-flash"):
        news_api = cls._get_upstox_api()
        if not news_api:
            logger.warning("Cannot fetch news: Upstox API unauthenticated.")
            return

        try:
            # Build instrument keys string
            active_symbols = set()
            for meta in watchlist.values():
                sym = meta.get("symbol", "").split('-')[0]
                if sym:
                    active_symbols.add(sym)
                    
            if not active_symbols:
                return

            import scrip_master_engine
            keys = ",".join([scrip_master_engine.get_instrument_key(sym) for sym in active_symbols])
            
            # Fetch from Upstox
            response = await asyncio.to_thread(
                news_api.get_news,
                category="instrument_keys",
                instrument_keys=keys
            )
            
            # Group by symbol
            recent_news = {}
            import scrip_master_engine
            reverse_map = {scrip_master_engine.get_instrument_key(sym): sym for sym in active_symbols}
            
            count = 0
            if hasattr(response, 'data') and isinstance(response.data, dict):
                for ik, news_list in response.data.items():
                    if not isinstance(news_list, list) or not news_list:
                        continue
                        
                    sym = reverse_map.get(ik)
                    if not sym:
                        continue
                        
                    for item in news_list:
                        count += 1
                        heading = item.get('heading', '')
                        summary = item.get('summary', '')
                        pub_time = item.get('published_time', 0)
                        
                        logger.info(f"Raw News -> Headline: {heading} | Keys: {ik}")
                        
                        try:
                            # pub_time is in milliseconds
                            ts = int(pub_time) / 1000
                        except:
                            ts = time.time()
                            
                        # Enforce 96-hour anti-hallucination window
                        if (time.time() - ts) > (96 * 3600):
                            continue
                            
                        if sym not in recent_news:
                            recent_news[sym] = []
                        if len(recent_news[sym]) < 3:
                            recent_news[sym].append({
                                "Article": f"Article {len(recent_news[sym]) + 1}",
                                "headline": heading,
                                "summary": summary
                            })
                            
            logger.info(f"--- RAW NEWS FETCHED ({count} items) ---")
            
            # Process with AI
            for sym in active_symbols:
                if sym in recent_news:
                    headline = recent_news[sym][0]['headline']
                    existing = cls.catalyst_cache.get(sym)
                    if existing and existing.get("raw_news") and existing["raw_news"][0]["headline"] == headline:
                        continue

                    logger.info(f"[NEWS] Found match for {sym}: {headline}")
                    news_data = {
                        "raw_news": recent_news[sym]
                    }
                    cls.catalyst_cache[sym] = news_data
                    logger.info(f"[CATALYST] {sym} -> {headline} (Raw)")
                else:
                    if sym not in cls.catalyst_cache or not cls.catalyst_cache[sym].get("raw_news"):
                        cls.catalyst_cache[sym] = {"raw_news": []}

        except Exception as e:
            logger.error(f"Error fetching Upstox news feeds: {e}")
        finally:
            cls.last_fetch_time = int(time.time())

    @classmethod
    async def instant_fetch(cls, model_name: str = None):
        if not cls.current_watchlist:
            logger.warning("No watchlist available for instant fetch.")
            return False
        model = model_name or cls.current_model
        logger.info(f"Triggering instant news fetch with {model}...")
        await cls.fetch_live_feeds(cls.current_watchlist, model)
        return True

    @classmethod
    async def fetch_symbol_news(cls, symbol: str, model_name: str = None):
        model = model_name or cls.current_model
        news_api = cls._get_upstox_api()
        if not news_api:
            logger.warning(f"Cannot fetch news for {symbol}: Upstox API unauthenticated.")
            return cls._fallback_payload(symbol)

        try:
            import scrip_master_engine
            ikey = scrip_master_engine.get_instrument_key(symbol)

            response = await asyncio.to_thread(
                news_api.get_news,
                category="instrument_keys",
                instrument_keys=ikey
            )
            
            count = 0
            all_items = []
            if hasattr(response, 'data') and isinstance(response.data, dict):
                all_items = response.data.get(ikey, [])
                
            if all_items:
                logger.info(f"--- RAW NEWS FETCHED ({len(all_items)} items for {symbol}) ---")
                for it in all_items:
                    logger.info(f"Raw News -> Headline: {it.get('heading', '')} | Keys: {ikey}")
                item = all_items[0]
                
                try:
                    pub_time = item.get('published_time', 0)
                    ts = int(pub_time) / 1000
                except:
                    ts = time.time()

                if (time.time() - ts) <= (96 * 3600):
                    heading = item.get('heading', '')
                    summary = item.get('summary', '')
                    logger.info(f"[NEWS FORCE FETCH] Match for {symbol}: {heading}")

                    news_data = {
                        "raw_news": [
                            {
                                "Article": f"Article {idx + 1}",
                                "headline": i.get('heading', ''),
                                "summary": i.get('summary', '')
                            }
                            for idx, i in enumerate(all_items[:3])
                        ]
                    }
                    cls.catalyst_cache[symbol] = news_data
                    return news_data
            
            # Anti-hallucination fallback
            news_data = cls._fallback_payload(symbol)
            cls.catalyst_cache[symbol] = news_data
            return news_data
        except Exception as e:
            logger.error(f"Error fetching specific news for {symbol}: {e}")
            return cls._fallback_payload(symbol)

    @classmethod
    async def _news_polling_loop(cls):
        logger.info(f"Starting background News Engine loop every {cls.current_interval}s with {cls.current_model}")
        while True:
            try:
                await cls.fetch_live_feeds(cls.current_watchlist, cls.current_model)
            except Exception as e:
                logger.error(f"News polling loop exception: {e}")
                
            await asyncio.sleep(cls.current_interval)

    @classmethod
    async def start_news_loop(cls, watchlist: dict, interval: int = 120, model_name: str = "gemini-2.5-flash"):
        cls.current_watchlist = watchlist
        cls.current_interval = interval
        cls.current_model = model_name
        
        await cls.stop_news_loop()
        
        cls.active_loop = asyncio.create_task(cls._news_polling_loop())
        return True

    @classmethod
    async def stop_news_loop(cls):
        if cls.active_loop and not cls.active_loop.done():
            cls.active_loop.cancel()
            logger.info("Stopped background News Engine loop.")
        cls.active_loop = None
        return True

    @classmethod
    async def analyze_macro_catalyst(cls, headlines: list, model_name: str = "gemini-2.5-flash"):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return "NEUTRAL", "Gemini API Key missing for macro analysis."
            
        try:
            client = genai.Client(api_key=api_key)
            news_text = "\n".join(headlines)
            prompt = (
                f"You are a quantitative macroeconomic analyst. Analyze these recent index headlines: \n{news_text}\n"
                "Summarize the overall global market context in exactly one concise paragraph. "
                "Also, assign a single Global_Sentiment enum from: [BULLISH, BEARISH, NEUTRAL, MIXED]. "
                "Format EXACTLY as:\nSENTIMENT: [ENUM]\nSUMMARY: [Your paragraph]"
            )
            
            response = await client.aio.models.generate_content(
                model=model_name,
                contents=prompt
            )
            
            text = response.text
            import re
            sentiment_match = re.search(r"SENTIMENT:\s*(BULLISH|BEARISH|NEUTRAL|MIXED)", text, re.IGNORECASE)
            summary_match = re.search(r"SUMMARY:\s*(.*)", text, re.IGNORECASE | re.DOTALL)
            
            sentiment = sentiment_match.group(1).upper() if sentiment_match else "NEUTRAL"
            summary = summary_match.group(1).strip() if summary_match else text.strip()
            
            return sentiment, summary
        except Exception as e:
            logger.error(f"Failed to analyze macro catalyst: {e}")
            return "NEUTRAL", "Error analyzing macro news."

    @classmethod
    async def fetch_macro_news(cls, model_name: str = "gemini-2.5-flash"):
        news_api = cls._get_upstox_api()
        if not news_api:
            logger.warning("Cannot fetch macro news: Upstox API unauthenticated.")
            return

        try:
            import scrip_master_engine
            # Upstox doesn't return news for index keys, so we use heavyweight proxy stocks for macro context
            proxy_symbols = ['RELIANCE', 'HDFCBANK', 'ICICIBANK', 'INFY', 'TCS', 'SBIN', 'ITC', 'LT', 'AXISBANK', 'KOTAKBANK']
            keys = ",".join([scrip_master_engine.get_instrument_key(sym) for sym in proxy_symbols if scrip_master_engine.get_instrument_key(sym)])
            
            response = await asyncio.to_thread(
                news_api.get_news,
                category="instrument_keys",
                instrument_keys=keys
            )
            
            headlines = []
            if hasattr(response, 'data') and isinstance(response.data, dict):
                for ik, news_list in response.data.items():
                    if not isinstance(news_list, list): continue
                    for item in news_list[:5]: # Take top 5 recent per index
                        try:
                            ts = int(item.get('published_time', 0)) / 1000
                            if (time.time() - ts) <= (96 * 3600):
                                heading = item.get('heading', '')
                                summary = item.get('summary', '')
                                deep_news_block = f"Headline: {heading} | Details: {summary}"
                                headlines.append(deep_news_block)
                        except: pass
                        
            if headlines:
                # Remove duplicates
                headlines = list(set(headlines))
                logger.info(f"--- MACRO NEWS FETCHED ({len(headlines)} items) ---")
                
                # Check cache to prevent redundant LLM calls if top headlines haven't changed
                current_top = "||".join(sorted(headlines))
                if getattr(cls, "_last_macro_headlines", "") == current_top:
                    logger.info("[MACRO NEWS] Skipping LLM call, no new macro headlines.")
                    return
                
                sentiment, summary = await cls.analyze_macro_catalyst(headlines, model_name)
                cls._last_macro_headlines = current_top
                
                cls.macro_context = {
                    "sentiment": sentiment,
                    "summary": summary,
                    "timestamp": int(time.time())
                }
                logger.info(f"[MACRO CONTEXT] {sentiment} - {summary[:50]}...")
        except Exception as e:
            logger.error(f"Error fetching macro news: {e}")

    @classmethod
    async def _macro_news_polling_loop(cls):
        logger.info("Starting background Macro News loop every 1800s")
        while True:
            try:
                await cls.fetch_macro_news(cls.current_model)
            except Exception as e:
                logger.error(f"Macro news polling loop exception: {e}")
            # Throttle to run every 30 minutes
            await asyncio.sleep(1800)

    @classmethod
    async def start_macro_news_loop(cls):
        if hasattr(cls, 'macro_loop') and cls.macro_loop and not cls.macro_loop.done():
            cls.macro_loop.cancel()
        cls.macro_loop = asyncio.create_task(cls._macro_news_polling_loop())
        return True

