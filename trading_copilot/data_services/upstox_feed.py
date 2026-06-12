import os
import sys
import json
import time
import asyncio
import logging
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

# Append trading_copilot to sys.path for relative imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("upstox_feed")

try:
    from pipeline_guard import is_market_open, PRODUCTION_LIVE
except ImportError:
    import sys, os
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from pipeline_guard import is_market_open, PRODUCTION_LIVE

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), '.env'))

import pyotp
import requests

try:
    from playwright.async_api import async_playwright
except ImportError:
    logger.warning("Playwright not installed, headless auth might fail.")

# Selectors (Configurable)
SELECTORS = {
    "mobile": ["input[type='tel']", "input[name='mobileNumber']", "input[placeholder*='Mobile']", "input[placeholder*='mobile']", "#mobileNum"],
    "otp": ["input[name='otp']", "input[type='number']", "input[type='text']", "#otpNum", "input[placeholder*='OTP']"],
    "pin": ["input[type='password']", "input[name='pin']", "#pin", "#pinCode", "input[name='pinCode']", "input[placeholder*='PIN']", "input[placeholder*='pin']"],
    "submit": ["button[type='submit']", "button:has-text('Get OTP')", "button:has-text('Continue')", "button:has-text('Submit')", ".btn-primary"]
}

class UpstoxAuthenticator:
    def __init__(self):
        self.client_id = os.getenv("UPSTOX_CLIENT_ID")
        self.client_secret = os.getenv("UPSTOX_CLIENT_SECRET")
        self.redirect_uri = os.getenv("UPSTOX_REDIRECT_URI", "http://localhost:8000/callback")
        self.mobile = os.getenv("UPSTOX_MOBILE_NO")
        self.pin = os.getenv("UPSTOX_PIN")
        self.totp_key = os.getenv("UPSTOX_TOTP_KEY")
        # Support both the root dir and the trading_copilot dir
        root_token = Path("upstox_token.json")
        copilot_token = Path(__file__).parent.parent / "upstox_token.json"
        self.token_file = copilot_token if copilot_token.exists() else root_token
    
    def _is_token_valid(self):
        if not self.token_file.exists():
            return False
        try:
            with open(self.token_file, "r") as f:
                data = json.load(f)
                timestamp = datetime.fromisoformat(data["timestamp"])
                if datetime.now() - timestamp < timedelta(hours=24):
                    logger.info("Found valid cached Upstox token.")
                    return data["access_token"]
        except Exception as e:
            logger.warning(f"Error reading token file: {e}")
        return False

    async def _find_and_fill(self, page, selector_list, value, name="field"):
        for sel in selector_list:
            try:
                # Use a short timeout to try multiple selectors
                element = await page.wait_for_selector(sel, state="visible", timeout=3000)
                if element:
                    await element.fill(value)
                    logger.info(f"Successfully filled {name} using selector: {sel}")
                    return True
            except:
                continue
        logger.error(f"Failed to find any working selector for {name}!")
        return False

    async def _find_and_click(self, page, selector_list, name="button"):
        for sel in selector_list:
            try:
                element = await page.wait_for_selector(sel, state="visible", timeout=3000)
                if element:
                    await element.click()
                    logger.info(f"Successfully clicked {name} using selector: {sel}")
                    return True
            except:
                continue
        logger.error(f"Failed to find any working selector for {name}!")
        return False

    async def _headless_login(self, auth_url):
        code = None
        async with async_playwright() as p:
            # Headless Switch
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context()
            page = await context.new_page()

            async def handle_request(route, request):
                nonlocal code
                if request.url.startswith(self.redirect_uri):
                    parsed_url = urllib.parse.urlparse(request.url)
                    query_params = urllib.parse.parse_qs(parsed_url.query)
                    if 'code' in query_params:
                        code = query_params['code'][0]
                        logger.info(f"Intercepted authorization code: {code}")
                    await route.abort()
                else:
                    await route.continue_()
            
            await context.route("**/*", handle_request)
            
            try:
                logger.info(f"Navigating to auth URL...")
                await page.goto(auth_url)
                
                # 1. Mobile Number
                await asyncio.sleep(2)
                await self._find_and_fill(page, SELECTORS["mobile"], self.mobile, "Mobile Number")
                await self._find_and_click(page, SELECTORS["submit"], "Submit Mobile")
                
                # 2. PIN (Upstox usually asks for PIN before TOTP now)
                await asyncio.sleep(2)
                await self._find_and_fill(page, SELECTORS["pin"], self.pin, "PIN")
                await self._find_and_click(page, SELECTORS["submit"], "Submit PIN")

                # 3. OTP
                logger.info("Generating TOTP...")
                await asyncio.sleep(2)
                totp = pyotp.TOTP(self.totp_key).now()
                await self._find_and_fill(page, SELECTORS["otp"], totp, "OTP")
                await self._find_and_click(page, SELECTORS["submit"], "Submit OTP")

                # Wait for redirect to happen
                for _ in range(15):
                    if code:
                        break
                    await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Headless login encountered an error: {e}")
            finally:
                await browser.close()
                
        return code

    def _manual_fallback(self, auth_url):
        logger.warning("\n--- TIER 2 LOCAL REDIRECT CATCHER FALLBACK ---")
        logger.warning(f"Please visit this URL in your browser: {auth_url}")
        logger.warning("After logging in, you will be redirected to an error page (localhost).")
        logger.warning("Copy the ENTIRE URL you are redirected to and paste it below.")
        redirected_url = input("Paste redirected URL: ").strip()
        parsed = urllib.parse.urlparse(redirected_url)
        params = urllib.parse.parse_qs(parsed.query)
        if 'code' in params:
            return params['code'][0]
        return None

    def _exchange_code(self, code):
        url = 'https://api.upstox.com/v2/login/authorization/token'
        headers = {
            'accept': 'application/json',
            'Api-Version': '2.0',
            'Content-Type': 'application/x-www-form-urlencoded'
        }
        data = {
            'code': code,
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'redirect_uri': self.redirect_uri,
            'grant_type': 'authorization_code'
        }
        
        response = requests.post(url, headers=headers, data=data)
        if response.status_code == 200:
            token_data = response.json()
            access_token = token_data.get('access_token')
            
            with open(self.token_file, "w") as f:
                json.dump({
                    "access_token": access_token,
                    "timestamp": datetime.now().isoformat()
                }, f)
            logger.info("Successfully exchanged code for access token and cached it.")
            return access_token
        else:
            logger.error(f"Failed to exchange token: {response.text}")
            return None

    async def get_valid_token(self):
        token = self._is_token_valid()
        if token:
            return token
            
        logger.info("Token expired or missing. Initiating authentication flow...")
        auth_url = f"https://api.upstox.com/v2/login/authorization/dialog?response_type=code&client_id={self.client_id}&redirect_uri={self.redirect_uri}"
        
        # Disabled Playwright headless login since it's too slow/brittle. 
        # Jumping straight to manual fallback.
        code = None

        if not code:
            code = self._manual_fallback(auth_url)
            
        if code:
            return self._exchange_code(code)
        return None

import upstox_client

class UpstoxStreamManager:
    live_market_data = {}

    def __init__(self, access_token, rolling_engine=None, reverse_map=None):
        self.access_token = access_token
        self.rolling_engine = rolling_engine
        self.reverse_map = reverse_map or {}
        
        # Configure Upstox API Client
        configuration = upstox_client.Configuration()
        configuration.access_token = access_token
        self.api_client = upstox_client.ApiClient(configuration)

        # Create separate instances for Tri-Stream
        self.stream_macro = upstox_client.MarketDataStreamerV3(api_client=self.api_client)
        self.stream_equity = upstox_client.MarketDataStreamerV3(api_client=self.api_client)
        self.stream_options = upstox_client.MarketDataStreamerV3(api_client=self.api_client)

    def _on_market_update(self, message):
        # Callback for all streams
        # 1. THE EXCHANGE TIME GATEWALK
        if not is_market_open():
            return
            
        # Expected protobuf dictionary parsed internally by Upstox SDK
        feeds = message.get("feeds")
        if feeds and isinstance(feeds, dict):
            for instrument_key, feed_data in feeds.items():
                if instrument_key not in UpstoxStreamManager.live_market_data:
                    UpstoxStreamManager.live_market_data[instrument_key] = {}
                if isinstance(feed_data, dict):
                    UpstoxStreamManager.live_market_data[instrument_key].update(feed_data)
                    
                    # Update O(1) Phantom Candle in Rolling Engine
                    if self.rolling_engine:
                        # Upstox protobuf to dict parser
                        # Check for various feed types (fullFeed, indexFF, optionGreeks)
                        full_feed = feed_data.get("fullFeed", {})
                        index_feed = feed_data.get("indexFF", {})
                        
                        market_ff = full_feed.get("marketFF", {})
                        index_ltpc = index_feed.get("ltpc", {})
                        
                        ltpc = market_ff.get("ltpc", index_ltpc)
                        
                        ltp = ltpc.get("ltp", 0)
                        
                        # Volume is at the root of marketFF as vtt, or inside marketFF depending on mode
                        vol = full_feed.get("vtt", market_ff.get("vtt", 0))
                        
                        # OI is typically inside marketFF
                        oi = market_ff.get("oi", 0)
                        
                        ts = int(time.time() * 1000)
                        
                        # Option Greeks might be at the root of feed_data (if optionGreeks mode) or inside fullFeed
                        greeks = feed_data.get("optionGreeks", full_feed.get("optionGreeks", {}))
                        
                        bids = []
                        asks = []
                        market_level = full_feed.get("marketLevel", market_ff.get("marketLevel", {}))
                        if market_level:
                            bid_ask_quote = market_level.get("bidAskQuote", [])
                            for quote in bid_ask_quote:
                                bids.append({'quantity': int(quote.get('bq', quote.get('bidQ', 0))), 'price': float(quote.get('bp', quote.get('bidP', 0)))})
                                asks.append({'quantity': int(quote.get('aq', quote.get('askQ', 0))), 'price': float(quote.get('ap', quote.get('askP', 0)))})
                        
                        # Map back to ws_token (e.g. NSE_EQ|SAIL) for the state engine
                        mapped_token = self.reverse_map.get(instrument_key, instrument_key)
                        
                        if ltp > 0:
                            # Pass directly, thread safe because Python dictionary updates are protected by GIL
                            self.rolling_engine.process_tick(
                                token=mapped_token,
                                timestamp_ms=ts,
                                price=float(ltp),
                                volume=float(vol),
                                oi=float(oi),
                                greeks=greeks,
                                bids=bids,
                                asks=asks
                            )
                            
        else:
            # Likely a status message or heartbeat
            logger.debug(f"Stream status message: {message}")

    def _on_error(self, message):
        logger.error(f"Streamer Error: {message}")

    def _on_close(self, code, reason):
        logger.warning(f"Streamer Closed: Code {code}, Reason: {reason}")

    def _setup_stream(self, streamer, name, instrument_keys, mode):
        def _on_open():
            logger.info(f"{name} stream connected! Subscribing to {len(instrument_keys)} keys in {mode} mode.")
            streamer.subscribe(instrument_keys, mode)
        
        streamer.on("open", _on_open)
        streamer.on("message", self._on_market_update)
        streamer.on("error", self._on_error)
        streamer.on("close", self._on_close)

    async def start_multiplexer(self, indices, equities, options):
        logger.info("Initializing Tri-Stream Multiplexer...")
        
        if not PRODUCTION_LIVE:
            logger.warning("PRODUCTION_LIVE is False! Entering Safe Testing Mode. Bypassing Upstox WebSocket.")
            asyncio.create_task(self._mock_feed_loop(indices, equities, options))
            return
            
        self._setup_stream(self.stream_macro, "Macro Pulse (Indices)", indices, "full")
        self._setup_stream(self.stream_equity, "Equity Tape", equities, "full_d30")
        self._setup_stream(self.stream_options, "Derivatives Matrix", options, "option_greeks")
        # Let them connect concurrently without blocking the main event loop
        import threading
        threading.Thread(target=self.stream_macro.connect).start()
        threading.Thread(target=self.stream_equity.connect).start()
        threading.Thread(target=self.stream_options.connect).start()
        
        logger.info("[SYSTEM] Upstox Tri-Stream separated. Macro Polling ENGAGED.")

    async def _mock_feed_loop(self, indices, equities, options):
        """Simulates incoming Upstox protobuf ticks using a static mock file."""
        import os, json, time, random
        mock_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'mock_ticks.json')
        
        if not os.path.exists(mock_file):
            logger.error(f"Safe Testing Mode Active, but {mock_file} not found. Cannot mock ticks.")
            return
            
        with open(mock_file, 'r') as f:
            mock_data = json.load(f)
            
        logger.info(f"Loaded {len(mock_data)} mock ticks. Beginning playback.")
        
        # Infinite playback loop
        while True:
            for tick in mock_data:
                # Update tick timestamp to now so engines don't reject it as stale
                try:
                    if 'feeds' in tick:
                        for k, v in tick['feeds'].items():
                            if 'fullFeed' in v and 'marketFF' in v['fullFeed'] and 'ltpc' in v['fullFeed']['marketFF']:
                                v['fullFeed']['marketFF']['ltpc']['ltt'] = str(int(time.time() * 1000))
                            # Add slight random noise to ltp to simulate movement
                            if 'fullFeed' in v and 'marketFF' in v['fullFeed'] and 'ltpc' in v['fullFeed']['marketFF']:
                                ltp = v['fullFeed']['marketFF']['ltpc'].get('ltp', 100.0)
                                v['fullFeed']['marketFF']['ltpc']['ltp'] = ltp * random.uniform(0.9995, 1.0005)
                except Exception:
                    pass
                
                # Push to the standard ingest pipeline
                self._on_market_update(tick)
                await asyncio.sleep(0.5) # 500ms Upstox tick rate

class MetricsCalculator:
    @staticmethod
    def calculate_pcr_max_pain(option_chain_data):
        # Live PCR Calculation Logic Placeholder
        return {"pcr": 1.2, "max_pain": 25000}

from fastapi import FastAPI, Request
import uvicorn
import csv

app = FastAPI(title="Upstox Tri-Stream Feed")

def make_json_serializable(obj):
    import numpy as np
    import pandas as pd
    if isinstance(obj, dict):
        return {str(k): make_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple, set)):
        return [make_json_serializable(v) for v in obj]
    elif isinstance(obj, (np.integer, np.int64, np.int32, np.int16, np.int8)):
        return int(obj)
    elif isinstance(obj, (np.floating, np.float64, np.float32, np.float16)):
        if np.isnan(obj) or np.isinf(obj): return 0.0
        return float(obj)
    return obj

@app.get("/state")
async def get_state():
    from diagnostic_ui import TerminalDashboard
    return make_json_serializable({"active_states": TerminalDashboard.active_states})

@app.post("/api/watchlist/update")
async def update_watchlist(request: Request):
    data = await request.json()
    items = data.get("items", [])
    
    if not hasattr(request.app.state, "watchlist"):
        return {"status": "error", "message": "Streamer not initialized yet"}
        
    watchlist = request.app.state.watchlist
    rolling_engine = request.app.state.rolling_engine
    stream_manager = request.app.state.stream_manager
    fetcher = request.app.state.fetcher
    upstox_api_client = request.app.state.upstox_api_client
    
    from historical_engine import HistoricalFetcher
    
    new_equities = []
    new_options = []
    new_watchlist_entries = {}
    
    import scrip_master_engine
    
    for item in items:
        token = str(item["token"])
        if token not in watchlist:
            new_watchlist_entries[token] = item
            watchlist[token] = item
            
            clean_sym = item['symbol'].split('-')[0]
            if item['exchange'] == "NSE":
                ikey = scrip_master_engine.get_instrument_key(clean_sym)
                new_equities.append(ikey)
                stream_manager.reverse_map[ikey] = f"NSE_EQ|{clean_sym}"
            elif item['exchange'] == "NFO":
                ikey = scrip_master_engine.get_instrument_key(clean_sym)
                new_options.append(ikey)
                stream_manager.reverse_map[ikey] = f"NSE_FO|{clean_sym}"

                
    if new_watchlist_entries:
        logger.info(f"Dynamically adding new tokens to feed: {list(new_watchlist_entries.keys())}")
        
        # 1. Fetch warmups
        new_warmups = await fetcher.fetch_batch_warmups(upstox_api_client, new_watchlist_entries)
        
        # 2. Add to rolling engine
        rolling_engine.watchlist.update(new_watchlist_entries)
        for k, v in new_warmups.items():
            rolling_engine.dfs[k] = v
                
        # 3. Subscribe live
        if new_equities:
            logger.info(f"Subscribing Equities: {new_equities}")
            stream_manager.stream_equity.subscribe(new_equities, "full_d30")
        if new_options:
            logger.info(f"Subscribing Options: {new_options}")
            stream_manager.stream_options.subscribe(new_options, "option_greeks")
            
    return {"status": "success", "added": list(new_watchlist_entries.keys())}

@app.post("/api/run-screener")
async def run_screener_api(request: Request):
    if not hasattr(request.app.state, "upstox_api_client"):
        return {"status": "error", "message": "Streamer not initialized yet"}
    try:
        from screener_engine import PreMarketScreener
        screener = PreMarketScreener(request.app.state.upstox_api_client)
        picks = await screener.run_scan()
        return {"status": "success", "data": picks}
    except Exception as e:
        logger.error(f"Screener Error: {e}")
        return {"status": "error", "message": str(e)}

async def start_upstox_service():
    auth = UpstoxAuthenticator()
    token = await auth.get_valid_token()
    if not token:
        logger.error("Failed to authenticate with Upstox. Exiting.")
        os._exit(1)

    indices = ["NSE_INDEX|Nifty 50", "NSE_INDEX|Nifty Bank"]
    
    # Load Watchlist
    WATCHLIST = {}
    try:
        csv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "watchlist.csv")
        if os.path.exists(csv_path):
            with open(csv_path, mode='r') as file:
                reader = csv.DictReader(file)
                for row in reader:
                    if not row.get('Symbol') or not row.get('Token'):
                        continue
                    WATCHLIST[row['Token']] = row
    except Exception as e:
        logger.error(f"Error reading watchlist: {e}")
        
    # Instantiate engine dependencies
    from historical_engine import HistoricalFetcher
    from rolling_state_engine import RollingStateEngine
    import upstox_client
    
    configuration = upstox_client.Configuration()
    configuration.access_token = token
    upstox_api_client = upstox_client.ApiClient(configuration)

    fetcher = HistoricalFetcher()
    warmup_dfs_map = await fetcher.fetch_batch_warmups(upstox_api_client, WATCHLIST)

    # Now that the maps are downloaded in historical_engine, build the true instrument keys
    equities = []
    options = []
    reverse_map = {}
    
    for row in WATCHLIST.values():
        clean_sym = row['Symbol'].split('-')[0]
        if row['Exchange'] == "NSE":
            ikey = HistoricalFetcher.upstox_eq_map.get(clean_sym, f"NSE_EQ|{clean_sym}")
            equities.append(ikey)
            reverse_map[ikey] = f"NSE_EQ|{clean_sym}"
        elif row['Exchange'] == "NFO":
            ikey = HistoricalFetcher.upstox_fo_map.get(clean_sym, f"NSE_FO|{clean_sym}")
            options.append(ikey)
            reverse_map[ikey] = f"NSE_FO|{clean_sym}"

    rolling_engine = RollingStateEngine(warmup_dfs_map, watchlist=WATCHLIST)
    stream_manager = UpstoxStreamManager(token, rolling_engine=rolling_engine, reverse_map=reverse_map)
    
    # Attach state to FastAPI app
    app.state.watchlist = WATCHLIST
    app.state.fetcher = fetcher
    app.state.upstox_api_client = upstox_api_client
    app.state.rolling_engine = rolling_engine
    app.state.stream_manager = stream_manager
    
    # Attach watchlist to API Client for derivatives_worker
    upstox_api_client.app_state_watchlist = WATCHLIST
    
    # Start derivatives background poller
    from derivatives_worker import derivatives_poller_loop
    asyncio.create_task(derivatives_poller_loop(upstox_api_client, list(WATCHLIST.keys()), HistoricalFetcher.upstox_eq_map))
    
    asyncio.create_task(stream_manager.start_multiplexer(indices, equities, options))
    asyncio.create_task(rolling_engine.calculate_technicals_loop())
    
    async def state_persistence_worker():
        while True:
            await asyncio.sleep(300) # Every 5 minutes
            if is_market_open():
                rolling_engine.save_cache()
                
    asyncio.create_task(state_persistence_worker())
    
    config = uvicorn.Config(app, host="127.0.0.1", port=8001, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(start_upstox_service())
    except KeyboardInterrupt:
        logger.info("Ctrl+C pressed. Shutting down Tri-Stream multiplexer...")
        os._exit(0)
