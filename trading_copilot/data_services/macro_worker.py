import asyncio
import logging
import time
import traceback
from datetime import datetime
import json
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pathlib import Path
from paths import BASE_DIR as _BASE_DIR, INSTITUTIONAL_FLOW_PATH, TOKEN_PATH, ensure_dirs
ensure_dirs()
STATE_FILE = INSTITUTIONAL_FLOW_PATH
import aiohttp
import upstox_client
from upstox_client.api.market_api import MarketApi
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_IST = ZoneInfo("Asia/Kolkata")

# Market breadth is an intraday metric -- unlike FII/DII it is worthless once
# a day old. Fetched on this cadence; NSE is not to be hit more often.
BREADTH_INTERVAL_S = 300

class InstitutionalFlowTracker:
    @staticmethod
    def save_state(fii_net: float, dii_net: float):
        # Market breadth comes from NSE's allIndices endpoint on its own
        # cadence (fetch_market_breadth, below) -- this writer must not
        # clobber it, so the stored ad_ratio and its fetch time are carried
        # through untouched.
        existing = InstitutionalFlowTracker.load_state()

        state = dict(existing)
        state.update({
            "timestamp": datetime.now(_IST).isoformat(),
            "fii_net": fii_net,
            "dii_net": dii_net,
        })
        state.setdefault("ad_ratio", 1.0)
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)

    @staticmethod
    def save_ad_ratio(ad_ratio: float):
        """Mirror image of save_state: write breadth without touching FII/DII.

        `ad_ratio_ts` stamps THIS value specifically. The general "timestamp"
        moves whenever FII/DII is written, so it cannot answer "is the breadth
        on screen fresh?" -- and a three-day-old A/D ratio must never be
        presented as today's market breadth.
        """
        existing = InstitutionalFlowTracker.load_state()
        state = dict(existing)
        state.update({
            "ad_ratio": round(float(ad_ratio), 2),
            "ad_ratio_ts": datetime.now(_IST).isoformat(),
        })
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=4)

    @staticmethod
    def load_state() -> dict:
        if not os.path.exists(STATE_FILE):
            return {"fii_net": 0, "dii_net": 0, "ad_ratio": 1.0}
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except:
            return {"fii_net": 0, "dii_net": 0, "ad_ratio": 1.0}

async def fetch_market_breadth():
    """NIFTY-50 advance/decline from NSE's allIndices endpoint.

    Ported from the retired macro_eod_engine.py (Task 5.6). This is the real
    NSE-wide breadth; the dashboard previously showed advances/declines over
    the ~27-symbol watchlist under an "NSE A/D Ratio" label.

    On a non-200 or an exception the stored value is left alone: an old
    ad_ratio that the UI can detect as stale beats a fabricated fresh one.
    """
    logger.info("Fetching NSE Market Breadth (A/D Ratio)...")
    url = "https://www.nseindia.com/api/allIndices"
    base_url = "https://www.nseindia.com"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            await session.get(base_url, timeout=10)
            await asyncio.sleep(3)  # Required to bypass Akamai anti-bot protection
            async with session.get(url, timeout=10) as response:
                if response.status != 200:
                    err_text = await response.text()
                    logger.critical(
                        f"Failed to fetch Market Breadth. Status Code: {response.status} - {err_text[:100]}")
                    return

                data = await response.json()
                indices = data.get("data", [])

                row = next((i for i in indices if i.get("index") == "NIFTY 50"), None)
                if row is None:
                    logger.warning("NSE allIndices response carried no NIFTY 50 row.")
                    return

                advances = float(row.get("advances", 0))
                declines = float(row.get("declines", 0))
                if advances == 0 and declines == 0:
                    ad_ratio = 1.0
                else:
                    if declines == 0:
                        declines = max(1.0, advances / 50.0)
                    ad_ratio = advances / max(1.0, declines)

                InstitutionalFlowTracker.save_ad_ratio(ad_ratio)
                logger.info(f"Market Breadth fetched from NIFTY 50. A/D Ratio: {ad_ratio:.2f}")
    except Exception as e:
        logger.error(f"Market Breadth Scraper Exception: {e}")


async def breadth_poller_loop():
    """Intraday cadence for market breadth, independent of the once-daily
    FII/DII fetch so the latter's long sleep cannot starve it.

    Gated on market hours. `ad_ratio_ts` is what gates the truthfulness of the
    dashboard's label, and it records FETCH time, not DATA time -- so polling
    overnight and at weekends would keep re-stamping the previous session's
    closing A/D as if it were minutes old. Skipping when the market is shut
    also drops ~288 pointless requests/day against an Akamai-protected
    endpoint. The stamp then ages out naturally and the card falls back to
    naming its proxy.
    """
    from pipeline_guard import is_market_open
    while True:
        if is_market_open():
            await fetch_market_breadth()
        await asyncio.sleep(BREADTH_INTERVAL_S)


import pytz

async def macro_poller_loop(api_client):
    """
    Background worker that fetches Upstox FII/DII endpoints natively.
    Runs once per day at 18:30 IST, and spawns the intraday market-breadth
    poller (Task 5.6) alongside it.
    """
    market_api = MarketApi(api_client)
    ist = pytz.timezone('Asia/Kolkata')

    # Breadth runs on its own 5-minute task: FII/DII sleeps until 18:30 IST.
    asyncio.create_task(breadth_poller_loop())
    
    while True:
        try:
            logger.info("Fetching native FII/DII data from Upstox...")
            
            # Fetch FII Cash
            fii_res = await asyncio.to_thread(market_api.get_fii_data, data_type='NSE_EQ|CASH', interval='1D')
            fii_dict = fii_res.data if hasattr(fii_res, 'data') else {}
            fii_data = fii_dict.get('NSE_EQ|CASH', []) if isinstance(fii_dict, dict) else []
            fii_net = 0.0
            if fii_data and len(fii_data) > 0:
                latest = fii_data[0]
                if hasattr(latest, 'to_dict'):
                    latest = latest.to_dict()
                elif not isinstance(latest, dict):
                    latest = getattr(latest, '__dict__', {})
                buy = latest.get('buy_amount', 0.0)
                sell = latest.get('sell_amount', 0.0)
                fii_net = round(buy - sell, 2)
                
            # Fetch DII Cash
            dii_res = await asyncio.to_thread(market_api.get_dii_data, data_type='NSE_EQ|CASH', interval='1D')
            dii_dict = dii_res.data if hasattr(dii_res, 'data') else {}
            dii_data = dii_dict.get('NSE_EQ|CASH', []) if isinstance(dii_dict, dict) else []
            dii_net = 0.0
            if dii_data and len(dii_data) > 0:
                latest = dii_data[0]
                if hasattr(latest, 'to_dict'):
                    latest = latest.to_dict()
                elif not isinstance(latest, dict):
                    latest = getattr(latest, '__dict__', {})
                buy = latest.get('buy_amount', 0.0)
                sell = latest.get('sell_amount', 0.0)
                dii_net = round(buy - sell, 2)
                
            logger.info(f"FII Net: {fii_net} Cr | DII Net: {dii_net} Cr")
            InstitutionalFlowTracker.save_state(fii_net, dii_net)
            
        except Exception as e:
            logger.error(f"Error in macro_poller_loop: {e}")
            logger.debug(traceback.format_exc())
            
        # Calculate time until next 18:30 IST
        now = datetime.now(ist)
        target = now.replace(hour=18, minute=30, second=0, microsecond=0)
        if now >= target:
            from datetime import timedelta
            target += timedelta(days=1)
        
        sleep_seconds = (target - now).total_seconds()
        logger.info(f"Macro worker sleeping for {sleep_seconds} seconds until next 18:30 IST run.")
        await asyncio.sleep(sleep_seconds)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import json
    import upstox_client
    with open(TOKEN_PATH) as f:
        token = json.load(f)['access_token']
        
    conf = upstox_client.Configuration()
    conf.access_token = token
    api = upstox_client.ApiClient(conf)
    asyncio.run(macro_poller_loop(api))
