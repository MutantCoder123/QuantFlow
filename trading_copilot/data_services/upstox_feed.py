import os
import sys
import json
import time
import asyncio
import logging
import urllib.parse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
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
from paths import REPO_ROOT, TOKEN_PATH, WATCHLIST_PATH, DATA_DIR, ensure_dirs
ensure_dirs()
load_dotenv(REPO_ROOT / '.env')

import pyotp
import requests
from llm import DEFAULT_MODEL

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

_IST = ZoneInfo("Asia/Kolkata")


def _now_ist() -> datetime:
    return datetime.now(_IST).replace(tzinfo=None)


def token_expiry(saved_at: datetime) -> datetime:
    """When a token saved at `saved_at` (naive IST) stops working.

    Upstox access tokens expire at 03:30 IST following issue, however
    recently they were issued: one saved at 00:18 dies at 03:30 the same
    morning. The old check assumed 24 h and kept a dead token "valid"."""
    cutoff = saved_at.replace(hour=3, minute=30, second=0, microsecond=0)
    return cutoff if saved_at < cutoff else cutoff + timedelta(days=1)


def jwt_expiry(token: str) -> datetime | None:
    """The token's own `exp` claim as naive IST, or None if it has none.

    Extended (analytics) tokens live for a year, so the 03:30 rule above
    must not be applied to them. The payload is read, not verified --
    Upstox verifies it; this only decides whether to ask for a login."""
    import base64
    try:
        seg = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))
        return datetime.fromtimestamp(int(claims["exp"]), _IST).replace(tzinfo=None)
    except (IndexError, KeyError, TypeError, ValueError):
        return None


class UpstoxAuthenticator:
    def __init__(self):
        self.client_id = os.getenv("UPSTOX_CLIENT_ID")
        self.client_secret = os.getenv("UPSTOX_CLIENT_SECRET")
        self.redirect_uri = os.getenv("UPSTOX_REDIRECT_URI", "http://localhost:8000/callback")
        self.mobile = os.getenv("UPSTOX_MOBILE_NO")
        self.pin = os.getenv("UPSTOX_PIN")
        self.totp_key = os.getenv("UPSTOX_TOTP_KEY")
        # Support both the root dir and the trading_copilot dir
        self.token_file = TOKEN_PATH
    
    def _is_token_valid(self):
        if not self.token_file.exists():
            return False
        try:
            with open(self.token_file, "r") as f:
                data = json.load(f)
                # The token's own expiry wins; otherwise the daily 03:30 rule
                # from when it was saved (naive IST; see _now_ist).
                expires = jwt_expiry(data["access_token"])
                if expires is None:
                    saved = datetime.fromisoformat(data["timestamp"]).replace(tzinfo=None)
                    expires = token_expiry(saved)
                if _now_ist() < expires:
                    logger.info(f"Found valid cached Upstox token (expires {expires:%Y-%m-%d %H:%M} IST).")
                    return data["access_token"]
                logger.warning(f"Cached Upstox token expired at {expires:%Y-%m-%d %H:%M} IST.")
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
                    "timestamp": _now_ist().isoformat()
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

from dataclasses import dataclass


@dataclass(frozen=True)
class Tick:
    token: str
    timestamp_ms: int
    price: float
    volume: float
    oi: float
    greeks: dict | None
    bids: list
    asks: list
    close_price: float | None = None   # LTPC "cp": the previous session's close
    day_ohlc: dict | None = None       # marketOHLC's "1d" row: today's open/high/low


def parse_tick(instrument_key: str, feed_data: dict, reverse_map: dict) -> "Tick | None":
    """Flatten one Upstox protobuf-dict feed entry into a Tick, or None if it
    carries no usable price.

    Pure function (module-level, no self) so it is unit-testable and so the
    WebSocket callback threads do nothing but parse-and-enqueue -- the actual
    mutation of phantom_candles / ltf_df happens on the single asyncio
    consumer (RollingStateEngine.ingest_loop). Fixes the three-OS-threads-
    mutate-shared-state hazard that was previously defended only by an
    incorrect "GIL makes this safe" comment (improved §4.10).
    """
    full = feed_data.get("fullFeed", {}) or {}
    # FullFeed is a oneof: a stock arrives as fullFeed.marketFF, an index as
    # fullFeed.indexFF. Reading indexFF at the top level dropped every index
    # tick, so the NIFTY price never reached the app (F1, 2026-09-29).
    idx = full.get("indexFF") or feed_data.get("indexFF") or {}
    mff = full.get("marketFF", {}) or {}
    ltpc = mff.get("ltpc") or idx.get("ltpc") or feed_data.get("ltpc") or {}
    ltp = float(ltpc.get("ltp", 0) or 0)
    if ltp <= 0:
        return None
    try:
        cp = float(ltpc.get("cp") or 0) or None
    except (TypeError, ValueError):
        cp = None
    day_ohlc = None
    for row in ((mff.get("marketOHLC") or idx.get("marketOHLC") or {}).get("ohlc") or []):
        if str(row.get("interval", "")).lower() == "1d":
            try:
                day_ohlc = {k: float(row[k]) for k in ("open", "high", "low")}
            except (KeyError, TypeError, ValueError):
                day_ohlc = None
            break

    bids, asks = [], []
    level = full.get("marketLevel", mff.get("marketLevel", {})) or {}
    for q in level.get("bidAskQuote", []) or []:
        bids.append({"quantity": int(q.get("bq", q.get("bidQ", 0)) or 0),
                     "price": float(q.get("bp", q.get("bidP", 0)) or 0)})
        asks.append({"quantity": int(q.get("aq", q.get("askQ", 0)) or 0),
                     "price": float(q.get("ap", q.get("askP", 0)) or 0)})

    return Tick(
        token=reverse_map.get(instrument_key, instrument_key),
        timestamp_ms=int(time.time() * 1000),
        price=ltp,
        volume=float(full.get("vtt", mff.get("vtt", 0)) or 0),
        oi=float(mff.get("oi", 0) or 0),
        greeks=feed_data.get("optionGreeks", full.get("optionGreeks")) or None,
        bids=bids, asks=asks, close_price=cp, day_ohlc=day_ohlc,
    )


class UpstoxStreamManager:
    live_market_data = {}

    def __init__(self, access_token, rolling_engine=None, reverse_map=None):
        self.access_token = access_token
        self.rolling_engine = rolling_engine
        self.reverse_map = reverse_map or {}
        # Set in start_multiplexer (which runs on the event loop) before any
        # WS thread starts; the callback threads use it to hop back onto the
        # loop safely.
        self.loop = None
        
        # Configure Upstox API Client
        configuration = upstox_client.Configuration()
        configuration.access_token = access_token
        self.api_client = upstox_client.ApiClient(configuration)

        # Create separate instances for Tri-Stream
        self.stream_macro = upstox_client.MarketDataStreamerV3(api_client=self.api_client)
        self.stream_equity = upstox_client.MarketDataStreamerV3(api_client=self.api_client)
        self.stream_options = upstox_client.MarketDataStreamerV3(api_client=self.api_client)

        # The key lists each supervisor re-subscribes on every reconnect.
        # add_subscription appends here, so a symbol added mid-session
        # survives a reconnect instead of silently falling off the feed.
        self.subscriptions = {"indices": [], "equity": [], "options": []}

    _STREAMS = {"equity": ("stream_equity", "full_d30"),
                "options": ("stream_options", "option_greeks")}

    def add_subscription(self, kind: str, keys: list) -> bool:
        """Add keys to a stream. True if subscribed now; False if the socket
        is down -- the keys are kept and subscribed on the next reconnect."""
        attr, mode = self._STREAMS[kind]
        current = self.subscriptions[kind]
        new = [k for k in keys if k not in current]
        if not new:
            return True
        current.extend(new)
        try:
            getattr(self, attr).subscribe(new, mode)
            return True
        except Exception as e:
            logger.warning(f"{kind} socket not open ({e}); {new} will subscribe on reconnect")
            return False

    def _on_market_update(self, message):
        # Callback for all streams. Runs on a WebSocket OS thread -- it must
        # NOT touch phantom_candles / ltf_df directly (that was the unsafe
        # part). Parse only, then hand the Tick to the single asyncio
        # consumer via the thread-safe loop hop.
        if not is_market_open():
            return

        feeds = message.get("feeds")
        if not isinstance(feeds, dict):
            logger.debug(f"Stream status message: {message}")
            return

        if self.rolling_engine is None or self.loop is None:
            return

        for ikey, feed in feeds.items():
            if not isinstance(feed, dict):
                continue
            tick = parse_tick(ikey, feed, self.reverse_map)
            if tick is not None:
                self.loop.call_soon_threadsafe(
                    self.rolling_engine.tick_q.put_nowait, tick)

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

    @staticmethod
    def socket_open(streamer) -> bool:
        """True while the streamer's WebSocket is actually connected."""
        ws = getattr(getattr(streamer, "feeder", None), "ws", None)
        sock = getattr(ws, "sock", None)
        return bool(sock is not None and getattr(sock, "connected", False))

    @staticmethod
    def _drop(streamer) -> None:
        ws = getattr(getattr(streamer, "feeder", None), "ws", None)
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    # Seconds a new socket gets to open, and how long it must stay up before
    # the backoff resets.
    OPEN_GRACE_S = 15
    HEALTHY_S = 60

    async def _supervise(self, name, streamer, keys, mode, sleep=asyncio.sleep, clock=time.monotonic):
        """Keep exactly one socket per stream alive (A-12: a dropped stream
        used to be silent and permanent).

        The SDK's connect() starts its own thread and returns at once, so
        liveness is read from the socket, not a thread. The SDK's own
        auto-reconnect is off: two reconnect owners each opened sockets
        until Upstox refused the handshake (403, too many connections) --
        seen on 2026-09-29. A stream with no keys opens no socket; it
        connects once add_subscription gives it some."""
        try:
            streamer.auto_reconnect(False)
        except Exception:
            pass
        self._setup_stream(streamer, name, keys, mode)
        backoff = 1
        while True:
            if not keys:
                await sleep(5)
                continue
            try:
                self._drop(streamer)
                streamer.connect()
                opened = clock()
                while not self.socket_open(streamer) and clock() - opened < self.OPEN_GRACE_S:
                    await sleep(0.5)
                up = clock()
                while self.socket_open(streamer):
                    await sleep(1)
                if clock() - up >= self.HEALTHY_S:
                    backoff = 1
            except Exception as e:
                logger.error(f"{name} supervisor error: {e}", exc_info=True)
            self._drop(streamer)
            logger.error(f"{name} stream down; reconnecting in {backoff}s")
            await sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def start_multiplexer(self, indices, equities, options):
        logger.info("Initializing Tri-Stream Multiplexer...")

        # Capture the running loop BEFORE any producer starts, so the WS
        # callback threads (and the mock loop) always have a valid target
        # for call_soon_threadsafe.
        self.loop = asyncio.get_running_loop()

        self.subscriptions["indices"][:] = list(indices)
        self.subscriptions["equity"][:] = list(equities)
        self.subscriptions["options"][:] = list(options)
        indices, equities, options = (self.subscriptions["indices"],
                                      self.subscriptions["equity"],
                                      self.subscriptions["options"])

        if not PRODUCTION_LIVE:
            logger.warning("PRODUCTION_LIVE is False! Entering Safe Testing Mode. Bypassing Upstox WebSocket.")
            asyncio.create_task(self._mock_feed_loop(indices, equities, options))
            return

        # Each stream gets its own supervisor task -- a drop on one reconnects
        # it without touching the other two.
        asyncio.create_task(self._supervise("Macro Pulse (Indices)", self.stream_macro, indices, "full"))
        asyncio.create_task(self._supervise("Equity Tape", self.stream_equity, equities, "full_d30"))
        asyncio.create_task(self._supervise("Derivatives Matrix", self.stream_options, options, "option_greeks"))

        logger.info("[SYSTEM] Upstox Tri-Stream separated. Macro Polling ENGAGED.")

    async def _mock_feed_loop(self, indices, equities, options):
        """Simulates incoming Upstox protobuf ticks using a static mock file."""
        import os, json, time, random
        mock_file = str(DATA_DIR / 'mock_ticks.json')
        
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
    eng = getattr(app.state, "rolling_engine", None)
    return make_json_serializable({
        "active_states": TerminalDashboard.active_states,
        "queue_depth": eng.queue_depth if eng is not None else -1,
    })


def build_bars_response(df, n: int = 300) -> dict:
    """Serialise the tail of an ltf_df for the /api/bars endpoint.

    Extracted as a pure function so it's unit-testable without a running
    FastAPI app or a live RollingStateEngine (Task 1.5, C-3b/c): the
    outcome resolver runs in the main.py process, while ltf_df lives only
    in this (upstox_feed.py) process's memory — this endpoint is the bridge
    that lets label_outcome see real bar highs/lows instead of the 60s LTP
    samples that were silently understating stop-hit rates.
    """
    if isinstance(df, list):                     # the index's bars (RollingStateEngine.index_bars)
        rows = df[-n:] if n > 0 else []
        return {"bars": [{**r, "timestamp": str(r["timestamp"])} for r in rows]}
    if df is None or df.empty:
        return {"bars": []}
    tail = df.tail(n).copy()
    tail["timestamp"] = tail["timestamp"].astype(str)
    cols = [c for c in ("timestamp", "open", "high", "low", "close", "volume") if c in tail.columns]
    return {"bars": tail[cols].to_dict(orient="records")}


@app.get("/api/bars")
async def get_bars(token: str, request: Request, n: int = 300):
    """Recent LTF bars for one token — used by SignalLedger's outcome
    resolver to label outcomes against real bar highs/lows (C-3b)."""
    eng = getattr(request.app.state, "rolling_engine", None)
    if eng is None:
        return {"bars": []}
    df = (eng.dfs.get(token) or {}).get("ltf_df")
    if df is None and token in getattr(eng, "index_bars", {}):
        df = list(eng.index_bars[token])
    return make_json_serializable(build_bars_response(df, n))

def _clean_symbol(row: dict) -> str:
    """One identity for a stock, whatever token or suffix a row carries.
    watchlist.csv and the scrip master disagree on both (INFY was 4494 in
    the file, 1594 in the master; SAIL-EQ vs SAIL)."""
    raw = row.get("Symbol") or row.get("symbol") or ""
    return str(raw).split("-")[0].strip().upper()


async def apply_watchlist_additions(state, items: list) -> list:
    """Bring new tokens live in this process: warm their history, register
    them with the rolling engine, and subscribe the websocket. Tokens already
    on the live watchlist are skipped. Returns the tokens actually added.

    Shared by the manual watchlist update and Discovery, so there is one way
    a symbol joins a running session."""
    watchlist = state.watchlist
    stream_manager = state.stream_manager

    import scrip_master_engine

    new_equities = []
    new_options = []
    new_watchlist_entries = {}
    live_symbols = {_clean_symbol(r) for r in watchlist.values()}

    for item in items:
        token = str(item["token"])
        sym = _clean_symbol(item)
        if token in watchlist or sym in live_symbols:
            continue
        live_symbols.add(sym)
        # Startup rows come from csv.DictReader over watchlist.csv
        # (Token/Symbol/Exchange). Carry both spellings so every reader --
        # _resolve_symbol, fetch_batch_warmups, derivatives_worker -- finds it.
        row = {"token": token, "symbol": item["symbol"], "exchange": item["exchange"],
               "Token": token, "Symbol": item["symbol"], "Exchange": item["exchange"]}
        new_watchlist_entries[token] = row
        watchlist[token] = row

        clean_sym = item["symbol"].split("-")[0]
        if item["exchange"] == "NSE":
            ikey = scrip_master_engine.get_instrument_key(clean_sym)
            new_equities.append(ikey)
            stream_manager.reverse_map[ikey] = f"NSE_EQ|{clean_sym}"
        elif item["exchange"] == "NFO":
            ikey = scrip_master_engine.get_instrument_key(clean_sym)
            new_options.append(ikey)
            stream_manager.reverse_map[ikey] = f"NSE_FO|{clean_sym}"

    if new_watchlist_entries:
        logger.info(f"Dynamically adding new tokens to feed: {list(new_watchlist_entries.keys())}")

        # 1. Fetch warmups
        new_warmups = await state.fetcher.fetch_batch_warmups(state.upstox_api_client, new_watchlist_entries)

        # 2. Add to rolling engine
        state.rolling_engine.watchlist.update(new_watchlist_entries)
        for k, v in new_warmups.items():
            state.rolling_engine.dfs[k] = v

        # 3. Subscribe live -- or, if the socket is down, on its reconnect
        pending = []
        if new_equities:
            logger.info(f"Subscribing Equities: {new_equities}")
            if not stream_manager.add_subscription("equity", new_equities):
                pending += new_equities
        if new_options:
            logger.info(f"Subscribing Options: {new_options}")
            if not stream_manager.add_subscription("options", new_options):
                pending += new_options
        state.subscription_pending = sorted(set(getattr(state, "subscription_pending", [])) | set(pending))

    return list(new_watchlist_entries.keys())


async def apply_discovery_selection(state, selected: list) -> tuple:
    """(live_added, removals_deferred, subscription_pending) for a Discovery selection.

    Additions go live now. Removals do not: unsubscribing mid-session would
    silently drop monitoring on a stock that may hold an open position.
    watchlist.csv already reflects the new selection, so a dropped symbol
    leaves at the next restart; until then it is reported, not hidden."""
    state.subscription_pending = []
    added = await apply_watchlist_additions(state, selected)
    keep = {_clean_symbol(s) for s in selected}
    deferred = [{"token": tok, "symbol": _clean_symbol(row)}
                for tok, row in state.watchlist.items() if _clean_symbol(row) not in keep]
    return added, deferred, list(state.subscription_pending)


@app.post("/api/watchlist/update")
async def update_watchlist(request: Request):
    data = await request.json()
    if not hasattr(request.app.state, "watchlist"):
        return {"status": "error", "message": "Streamer not initialized yet"}
    added = await apply_watchlist_additions(request.app.state, data.get("items", []))
    return {"status": "success", "added": added}


from discovery_job import DiscoveryJob

discovery = DiscoveryJob()


@app.post("/api/discovery/run")
async def run_discovery(request: Request):
    """Start a Discovery run: scan, apply to the live feed, then one LLM
    call over the top picks. Returns at once; poll /api/discovery/status.
    Optional body: {"model": "<gemini model>"}."""
    state = request.app.state
    if not hasattr(state, "upstox_api_client"):
        return {"status": "error", "message": "Streamer not initialized yet"}

    try:
        body = await request.json()
    except Exception:
        body = {}
    model = (body or {}).get("model") or DEFAULT_MODEL

    from screener_engine import PreMarketScreener
    from discovery_analysis import analyze_top
    screener = PreMarketScreener(state.upstox_api_client)

    async def scan(progress):
        picks = await screener.run_scan(progress=progress)
        return picks, screener.selected

    async def apply(selected):
        return await apply_discovery_selection(state, selected)

    import aiohttp

    async def fetch_news(symbol):
        # The news process (port 8003) only watches the watchlist; ask it for
        # this symbol directly. Raw Upstox news -- no LLM on that side.
        async with aiohttp.ClientSession() as session:
            async with session.post(f"http://127.0.0.1:8003/api/news/fetch/{symbol}", json={},
                                    timeout=aiohttp.ClientTimeout(total=15)) as resp:
                body = await resp.json()
        return body.get("data") if body.get("status") == "success" else None

    async def analyze(picks):
        from discovery_analysis import fill_news
        await fill_news(picks, fetch_news)
        return await analyze_top(picks, model=model)

    started = discovery.start(scan, apply, analyze)
    return {"status": "started" if started else "already_running",
            "job": discovery.status()}


@app.get("/api/discovery/status")
async def discovery_status():
    return {"status": "success", "job": discovery.status()}

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
        csv_path = str(WATCHLIST_PATH)
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
    
    # Single consumer: drains rolling_engine.tick_q and is the ONLY thing
    # that calls process_tick (improved §4.10). Start it before the
    # multiplexer so no enqueued tick waits on a not-yet-running consumer.
    asyncio.create_task(rolling_engine.ingest_loop())
    asyncio.create_task(stream_manager.start_multiplexer(indices, equities, options))
    asyncio.create_task(rolling_engine.calculate_technicals_loop())
    
    async def state_persistence_worker():
        while True:
            await asyncio.sleep(300) # Every 5 minutes
            if is_market_open():
                # ~6 MB JSON dump -- off the event loop thread (D-2).
                await asyncio.to_thread(rolling_engine.save_cache)
                
    asyncio.create_task(state_persistence_worker())

    async def tick_flush_worker():
        # One failed flush must not end tick recording for the session.
        while True:
            await asyncio.sleep(30)
            try:
                await asyncio.to_thread(rolling_engine.recorder.flush)
            except Exception as e:
                logger.error(f"Tick flush failed: {e}", exc_info=True)

    asyncio.create_task(tick_flush_worker())
    
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
