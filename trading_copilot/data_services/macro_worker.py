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
_BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATE_FILE = _BASE_DIR / 'data' / 'institutional_flow.json'
import upstox_client
from upstox_client.api.market_api import MarketApi

logger = logging.getLogger(__name__)

class InstitutionalFlowTracker:
    @staticmethod
    def save_state(fii_net: float, dii_net: float):
        # Calculate ad_ratio (market breadth) randomly or leave at 1.0 since it was in nse_feed
        # We don't have Market Breadth natively from Upstox Market Information yet unless we hit another endpoint.
        # For now, preserve existing ad_ratio if available
        existing = InstitutionalFlowTracker.load_state()
        ad_ratio = existing.get("ad_ratio", 1.0)
        
        state = {
            "timestamp": datetime.now().isoformat(),
            "fii_net": fii_net,
            "dii_net": dii_net,
            "ad_ratio": ad_ratio
        }
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

import pytz

async def macro_poller_loop(api_client):
    """
    Background worker that fetches Upstox FII/DII endpoints natively.
    Runs once per day at 18:30 IST.
    """
    market_api = MarketApi(api_client)
    ist = pytz.timezone('Asia/Kolkata')
    
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
    with open(os.path.join(_BASE_DIR, 'upstox_token.json')) as f:
        token = json.load(f)['access_token']
        
    conf = upstox_client.Configuration()
    conf.access_token = token
    api = upstox_client.ApiClient(conf)
    asyncio.run(macro_poller_loop(api))
