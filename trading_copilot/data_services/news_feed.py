import os
import sys
import asyncio
import logging
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(level=logging.INFO, format='[%(levelname)s %(asctime)s %(module)s:%(lineno)d] %(message)s')
logger = logging.getLogger(__name__)

from news_engine import NewsEngine
from config import load_watchlist_from_csv

app = FastAPI(title="News Scraper Daemon")

csv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "watchlist.csv")
WATCHLIST = load_watchlist_from_csv(csv_path)

class NewsInstantRequest(BaseModel):
    model: str = "gemini-2.5-flash"

class NewsStartRequest(BaseModel):
    interval: int = 120
    model: str = "gemini-2.5-flash"

def make_json_serializable(obj):
    if isinstance(obj, dict):
        return {str(k): make_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple, set)):
        return [make_json_serializable(v) for v in obj]
    return obj

@app.get("/state")
async def get_state():
    payload = {
        "status": "success",
        "catalyst_cache": NewsEngine.catalyst_cache,
        "is_active": NewsEngine.active_loop is not None,
        "interval": NewsEngine.current_interval,
        "model": NewsEngine.current_model,
        "last_fetch_time": getattr(NewsEngine, 'last_fetch_time', 0),
        "macro_context": NewsEngine.macro_context
    }
    return make_json_serializable(payload)

@app.post("/api/news/instant")
async def instant_news_fetch_api(req: NewsInstantRequest):
    await NewsEngine.instant_fetch(req.model)
    return {"status": "success"}

@app.post("/api/news/fetch/{symbol}")
async def fetch_symbol_news_api(symbol: str, req: NewsInstantRequest):
    news_data = await NewsEngine.fetch_symbol_news(symbol, req.model)
    if news_data:
        return {"status": "success", "data": news_data}
    return {"status": "error", "message": "Failed"}

@app.post("/api/news/loop/start")
async def start_news_loop_api(req: NewsStartRequest):
    await NewsEngine.start_news_loop(WATCHLIST, req.interval, req.model)
    return {"status": "success"}

@app.post("/api/news/loop/stop")
async def stop_news_loop_api():
    await NewsEngine.stop_news_loop()
    return {"status": "success"}

async def start_service():
    logger.info("Starting News Feed (Port 8003)...")
    await NewsEngine.start_macro_news_loop()
    await NewsEngine.start_news_loop(WATCHLIST, interval=120, model_name="gemini-2.5-flash")
    config = uvicorn.Config(app, host="127.0.0.1", port=8003, log_level="warning")
    server = uvicorn.Server(config)
    try:
        await server.serve()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Shutting down News feed...")
        os._exit(0)

async def test_news_api():
    logger.info("Testing Upstox News API integration...")
    res_reliance = await NewsEngine.fetch_symbol_news("RELIANCE")
    logger.info(f"RELIANCE Catalyst: {res_reliance}")
    
    res_nifty = await NewsEngine.fetch_symbol_news("NIFTY")
    logger.info(f"NIFTY Catalyst: {res_nifty}")
    
    logger.info("Starting live service...")
    await start_service()

if __name__ == "__main__":
    try: 
        asyncio.run(test_news_api())
    except KeyboardInterrupt: 
        os._exit(0)
