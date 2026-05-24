import os
import asyncio
import logging
import feedparser
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

class NewsEngine:
    catalyst_cache = {}
    
    # Pre-defined mapping of base symbols to news keywords
    SYMBOL_KEYWORDS = {
        "SAIL": ["SAIL", "Steel Authority"],
        "VEDL": ["Vedanta", "VEDL"],
        "TRITURBINE": ["Triveni Turbine", "Triveni"],
        "NETWEB": ["Netweb", "Net Wave"],
        "HINDCOPPER": ["Hindustan Copper", "Hindcopper"],
        "TEJASNET": ["Tejas Networks", "Tejas"],
        "RICOAUTO": ["Rico Auto", "Rico"],
        "INFY": ["Infosys", "INFY"],
        "TCS": ["TCS", "Tata Consultancy"],
        "BHARTIARTL": ["Airtel", "Bharti"],
        "RELIANCE": ["Reliance", "RIL", "Ambani"],
        "HDFCBANK": ["HDFC", "HDFC Bank"],
        "ICICIBANK": ["ICICI", "ICICI Bank"],
        "SBIN": ["SBI", "State Bank"],
        "TATAMOTORS": ["Tata Motors", "TMPV"],
        "TMPV": ["Tata Motors", "TMPV"],
        "NIFTY": ["Nifty", "Markets", "Sensex"]
    }

    @classmethod
    async def analyze_catalyst(cls, headline: str, summary: str):
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
            
            # Using the exact model requested by the user
            response = await client.aio.models.generate_content(
                model="gemini-3-flash",
                contents=prompt
            )
            return response.text.strip()
        except Exception as e:
            logger.error(f"Gemini API error during news parsing: {e}")
            return "NEUTRAL - Catalyst parsing failed due to API error."

    @classmethod
    async def fetch_live_feeds(cls, watchlist: dict):
        url = "https://www.moneycontrol.com/rss/latestnews.xml"
        try:
            # feedparser.parse is blocking, so we run it in a thread
            feed = await asyncio.to_thread(feedparser.parse, url)
            
            if not feed or not feed.entries:
                return
                
            latest_items = feed.entries[:20]
            
            # Build a list of active parent symbols from the watchlist
            active_symbols = set()
            for meta in watchlist.values():
                sym = meta.get("symbol", "").split('-')[0]
                if sym:
                    active_symbols.add(sym)
                    
            # For each active symbol, check if it was mentioned in the latest news
            for sym in active_symbols:
                keywords = cls.SYMBOL_KEYWORDS.get(sym, [sym])
                
                # Check for match in latest 20 feeds
                matched_item = None
                for item in latest_items:
                    title = item.get('title', '').upper()
                    desc = item.get('description', '').upper()
                    
                    if any(kw.upper() in title or kw.upper() in desc for kw in keywords):
                        matched_item = item
                        break
                        
                if matched_item:
                    # We have a catalyst hit! Send it to Gemini
                    logger.info(f"[NEWS] Found match for {sym}: {matched_item.get('title')}")
                    catalyst = await cls.analyze_catalyst(matched_item.get('title', ''), matched_item.get('description', ''))
                    cls.catalyst_cache[sym] = catalyst
                    logger.info(f"[CATALYST] {sym} -> {catalyst}")
                else:
                    # Maintain memory, or set default if not tracked
                    if sym not in cls.catalyst_cache:
                        cls.catalyst_cache[sym] = "NEUTRAL - No immediate fundamental catalyst detected."
                        
        except Exception as e:
            logger.error(f"Error fetching RSS feeds: {e}")

    @classmethod
    async def start_news_polling(cls, watchlist: dict):
        logger.info("Initializing High-Frequency Autonomous News Engine (Phase 7)...")
        while True:
            try:
                await cls.fetch_live_feeds(watchlist)
            except Exception as e:
                logger.error(f"News polling loop exception: {e}")
                
            # Sleep for exactly 120 seconds (2 minutes)
            await asyncio.sleep(120)
