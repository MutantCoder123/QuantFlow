import asyncio
import aiohttp
import json
import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class InstitutionalFlowTracker:
    STATE_FILE = "fii_dii_state.json"

    @classmethod
    def load_state(cls):
        if not os.path.exists(cls.STATE_FILE):
            return {"fii_net": 0, "dii_net": 0, "date": "N/A"}
        with open(cls.STATE_FILE, 'r') as f:
            try:
                return json.load(f)
            except Exception:
                return {"fii_net": 0, "dii_net": 0, "date": "N/A"}

    @classmethod
    def save_state(cls, data):
        with open(cls.STATE_FILE, 'w') as f:
            json.dump(data, f)

    async def fetch_nse_data(self):
        logger.info("Fetching FII/DII daily flow from NSE...")
        url = "https://www.nseindia.com/api/fiidiiTradeReact"
        base_url = "https://www.nseindia.com"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        }
        
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                # Step 1: The Handshake
                await session.get(base_url, timeout=10)
                
                # Step 2: The Data Fetch
                async with session.get(url, timeout=10) as response:
                    if response.status != 200:
                        err_text = await response.text()
                        logger.critical(f"Failed to fetch FII/DII data. Status Code: {response.status} - Response: {err_text[:200]}")
                        self.save_state({"fii_net": 0.0, "dii_net": 0.0, "date": datetime.now().strftime("%d-%b-%Y")})
                        return

                    data = await response.json()
                    fii_net = 0.0
                    dii_net = 0.0
                    date_str = datetime.now().strftime("%d-%b-%Y")
                    
                    records = data if isinstance(data, list) else data.get('data', [])
                    
                    if not records:
                        logger.warning("NSE FII/DII API returned empty data block.")
                        self.save_state({"fii_net": 0.0, "dii_net": 0.0, "date": date_str})
                        return
                        
                    for item in records:
                        cat = item.get('category', '').upper()
                        net_val = str(item.get('netValue', '0')).replace(',', '')
                        
                        try:
                            val = float(net_val)
                        except ValueError:
                            val = 0.0
                            
                        if 'FII' in cat:
                            fii_net = val
                            if 'date' in item:
                                date_str = item['date']
                        elif 'DII' in cat:
                            dii_net = val
                    
                    state = {
                        "fii_net": fii_net,
                        "dii_net": dii_net,
                        "date": date_str
                    }
                    self.save_state(state)
                    logger.info(f"FII/DII data successfully updated: FII={fii_net} Cr, DII={dii_net} Cr")
                    
        except Exception as e:
            logger.error(f"FII/DII Scraper Exception: {e}")
            self.save_state({"fii_net": 0.0, "dii_net": 0.0, "date": datetime.now().strftime("%d-%b-%Y")})

    async def fetch_market_breadth(self):
        logger.info("Fetching NSE Market Breadth (A/D Ratio)...")
        url = "https://www.nseindia.com/api/marketStatus"
        base_url = "https://www.nseindia.com"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
        }
        
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                await session.get(base_url, timeout=10)
                async with session.get(url, timeout=10) as response:
                    if response.status != 200:
                        err_text = await response.text()
                        logger.critical(f"Failed to fetch Market Breadth. Status Code: {response.status} - {err_text[:100]}")
                        return
                        
                    data = await response.json()
                    market_state = data.get("marketState", [])
                    
                    ad_ratio = 1.0
                    for item in market_state:
                        if item.get("market", "") == "Capital Market":
                            advances = float(item.get("advances", 0))
                            declines = float(item.get("declines", 0))
                            
                            if advances == 0 and declines == 0:
                                ad_ratio = 1.0
                            else:
                                if declines == 0: declines = max(1.0, advances / 50.0)
                                ad_ratio = advances / max(1.0, declines)
                            break
                    
                    state = self.load_state()
                    state["ad_ratio"] = round(ad_ratio, 2)
                    self.save_state(state)
                    logger.info(f"Market Breadth fetched. A/D Ratio: {ad_ratio:.2f}")
        except Exception as e:
            logger.error(f"Market Breadth Scraper Exception: {e}")

    async def start_daily_cron(self):
        logger.info("Initializing FII/DII End-of-Day Institutional Cron...")
        
        # Phase 6: Always do a hard fetch on boot to ensure UI is populated
        await self.fetch_nse_data()
        
        while True:
            # Phase 6: Fetch market breadth frequently (e.g., every 5 mins)
            await self.fetch_market_breadth()
            
            now = datetime.now()
            # If after 18:30 IST
            if now.hour > 18 or (now.hour == 18 and now.minute >= 30):
                current_date = now.strftime("%d-%b-%Y")
                state = self.load_state()
                
                # Check if data for today has been fetched yet
                if state.get("date") != current_date:
                    await self.fetch_nse_data()
            
            # Wake up every 5 minutes
            await asyncio.sleep(300)
