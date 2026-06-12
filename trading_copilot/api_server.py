import os
import asyncio
import logging
import csv
import aiohttp
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

logger = logging.getLogger(__name__)

from diagnostic_ui import TerminalDashboard
from config import load_watchlist_from_csv
from reasoning_engine import ReasoningEngine
from history_manager import HistoryManager

app = FastAPI(title="AlgoTrade Live Web Portal")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

watchlist_path = os.path.join(os.path.dirname(__file__), "watchlist.csv")
watchlist = load_watchlist_from_csv(watchlist_path)

local_active_states = {}
local_macro_state = {}
local_stock_derivatives_state = {}
local_fii_dii_state = {}
local_catalyst_cache = {}
local_macro_context = None

async def poll_upstox():
    global local_active_states, watchlist
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get("http://127.0.0.1:8001/state", timeout=2) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        local_active_states = data.get("active_states", {})
                        # Option: We can let api_server manage its own watchlist from csv
            except: pass
            await asyncio.sleep(0.5)

async def poll_nse():
    global local_macro_state, local_stock_derivatives_state, local_fii_dii_state
    import os, json
    flow_file = os.path.join(os.path.dirname(__file__), 'data', 'institutional_flow.json')
    while True:
        try:
            if os.path.exists(flow_file):
                with open(flow_file, 'r') as f:
                    data = json.load(f)
                    local_fii_dii_state = data
                    local_macro_state = data # Fallback for backwards compat
        except: pass
        await asyncio.sleep(5)

async def poll_news():
    global local_catalyst_cache
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get("http://127.0.0.1:8003/state", timeout=2) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        local_catalyst_cache = data.get("catalyst_cache", {})
                        global local_macro_context
                        local_macro_context = data.get("macro_context")
                        TerminalDashboard.catalyst_cache = local_catalyst_cache
            except: pass
            await asyncio.sleep(0.5)

@app.on_event("startup")
async def startup_event():
    print("[SYSTEM] Upstox Tri-Stream separated. Macro Polling ENGAGED.")
    asyncio.create_task(poll_upstox())
    asyncio.create_task(poll_nse())
    asyncio.create_task(poll_news())
    asyncio.create_task(ReasoningEngine.start_global_gatekeeper_loop())

async def proxy_post(port: int, endpoint: str, payload: dict = None, timeout: int = 300):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"http://127.0.0.1:{port}{endpoint}", json=payload, timeout=timeout) as resp:
                return await resp.json()
    except Exception as e:
        logger.error(f"Proxy request failed to {endpoint}: {e}")
        return {"status": "error", "message": f"Service on {port} unreachable: {e}"}

async def proxy_get(port: int, endpoint: str, timeout: int = 30):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}{endpoint}", timeout=timeout) as resp:
                return await resp.json()
    except Exception as e:
        logger.error(f"Proxy request failed to {endpoint}: {e}")
        return {"status": "error", "message": f"Service on {port} unreachable: {e}"}


@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    template_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    except FileNotFoundError:
        return HTMLResponse(content="<h1>templates/index.html not found.</h1>", status_code=404)

@app.post("/api/run-screener")
async def run_screener(): return await proxy_post(8001, "/api/run-screener")

@app.post("/api/map-option-tokens")
async def map_option_tokens(): return await proxy_post(8001, "/api/map-option-tokens")

class InstantAnalyzeRequest(BaseModel):
    model: str = "gemini-2.5-flash"
    prompt: str = ""
    user_position: dict | None = None
    user_intent: dict | None = None

class LoopStartRequest(BaseModel):
    symbol: str
    interval: int = 90
    model: str = "gemini-2.5-flash"
    prompt: str = ""
    user_position: dict | None = None
    user_intent: dict | None = None

class LoopStopRequest(BaseModel):
    symbol: str

class SavePositionRequest(BaseModel):
    symbol: str
    user_position: dict | None = None

@app.post("/api/reasoning/position/save")
async def save_position_api(req: SavePositionRequest):
    ReasoningEngine.user_positions[req.symbol] = req.user_position
    return {"status": "success"}

@app.post("/api/reasoning/instant/{symbol}")
async def instant_analyze(symbol: str, req: InstantAnalyzeRequest):
    TerminalDashboard.active_states = local_active_states
    report = await ReasoningEngine.analyze_stock(symbol, req.model, req.prompt, req.user_position, req.user_intent)
    return {"status": "success", "report": report}

@app.post("/api/reasoning/loop/start")
async def start_analysis_loop(req: LoopStartRequest):
    TerminalDashboard.active_states = local_active_states
    ReasoningEngine.user_positions[req.symbol] = req.user_position
    ReasoningEngine.set_llm_toggle(req.symbol, True, req.user_position)
    return {"status": "success"}

@app.post("/api/reasoning/loop/stop")
async def stop_analysis_loop(req: LoopStopRequest):
    ReasoningEngine.set_llm_toggle(req.symbol, False)
    return {"status": "success"}

@app.get("/api/reasoning/all_reports")
async def get_all_reports():
    return {"status": "success", "reports": ReasoningEngine.latest_reports}

@app.get("/api/reasoning/report/{symbol}")
async def get_latest_report(symbol: str):
    report = ReasoningEngine.latest_reports.get(symbol, "No report generated yet.")
    return {"status": "success", "report": report, "is_active": symbol in ReasoningEngine.active_loops}

class NewsInstantRequest(BaseModel): model: str = "gemini-2.5-flash"
class NewsStartRequest(BaseModel): interval: int = 120; model: str = "gemini-2.5-flash"

@app.post("/api/reasoning/playbook/generate")
async def generate_playbook(req: NewsInstantRequest):
    import asyncio
    asyncio.create_task(ReasoningEngine.generate_intraday_playbook(req.model))
    return {"status": "success", "message": "Playbook generation triggered in background."}

@app.post("/api/news/instant")
async def instant_news_fetch(req: NewsInstantRequest): return await proxy_post(8003, "/api/news/instant", {"model": req.model})

@app.post("/api/news/fetch/{symbol}")
async def fetch_symbol_news_api(symbol: str, req: NewsInstantRequest): return await proxy_post(8003, f"/api/news/fetch/{symbol}", {"model": req.model})

@app.post("/api/news/loop/start")
async def start_news_loop_api(req: NewsStartRequest): return await proxy_post(8003, "/api/news/loop/start", {"interval": req.interval, "model": req.model})

@app.post("/api/news/loop/stop")
async def stop_news_loop_api(): return await proxy_post(8003, "/api/news/loop/stop")

@app.get("/api/news/state")
async def get_news_state(): return await proxy_get(8003, "/state")

class SyncParquetRequest(BaseModel):
    symbols: list[str]

@app.post("/api/admin/sync-parquet")
async def sync_parquet_endpoint(req: SyncParquetRequest):
    from data_services.parquet_engine import sync_eod_parquet
    # Fire and forget
    asyncio.create_task(sync_eod_parquet(req.symbols))
    return {"status": "success", "message": "Parquet sync started in the background."}

@app.get("/api/search-token")
async def search_token_api(q: str):
    import scrip_master_engine
    results = await scrip_master_engine.search_scrip_tokens(q)
    return {"status": "success", "data": results}

@app.get("/api/watchlist")
async def get_watchlist(): return {"status": "success", "data": [{"token": k, "symbol": v["symbol"], "exchange": v["exchange"]} for k, v in watchlist.items()]}

class WatchlistUpdateRequest(BaseModel): items: list[dict]

@app.post("/api/watchlist")
async def update_watchlist(req: WatchlistUpdateRequest):
    global watchlist
    try:
        with open(watchlist_path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Token", "Symbol", "Exchange"])
            for item in req.items: writer.writerow([item["token"], item["symbol"], item["exchange"]])
            
        new_symbols = []
        for item in req.items:
            if item["token"] not in watchlist:
                new_symbols.append(item["symbol"].split('-')[0])
                
        watchlist.clear()
        for item in req.items:
            watchlist[item["token"]] = {"symbol": item["symbol"], "exchange": item["exchange"]}
            
        if new_symbols:
            logger.info(f"Triggering background Parquet sync for new symbols: {new_symbols}")
            from data_services.parquet_engine import sync_eod_parquet
            asyncio.create_task(sync_eod_parquet(new_symbols))
            
    except Exception as e: return {"status": "error", "message": str(e)}
    return await proxy_post(8001, "/api/watchlist/update", {"items": req.items})

class WatchlistAddRequest(BaseModel):
    token: str
    symbol: str
    exchange: str

@app.post("/api/watchlist/add")
async def add_to_watchlist(req: WatchlistAddRequest):
    global watchlist
    if req.token in watchlist:
        return {"status": "success", "message": "Already in watchlist."}
    
    current_items = [{"token": k, "symbol": v["symbol"], "exchange": v["exchange"]} for k, v in watchlist.items()]
    current_items.append({"token": req.token, "symbol": req.symbol, "exchange": req.exchange})
    
    update_req = WatchlistUpdateRequest(items=current_items)
    return await update_watchlist(update_req)

def make_json_serializable(obj):
    import numpy as np, pandas as pd
    if isinstance(obj, dict): return {str(k): make_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple, set)): return [make_json_serializable(v) for v in obj]
    elif isinstance(obj, (np.integer, np.int64, np.int32, np.int16, np.int8)): return int(obj)
    elif isinstance(obj, (np.floating, np.float64, np.float32, np.float16)): return 0.0 if np.isnan(obj) or np.isinf(obj) else float(obj)
    elif isinstance(obj, np.ndarray): return make_json_serializable(obj.tolist())
    elif isinstance(obj, pd.Timestamp): return obj.isoformat()
    elif isinstance(obj, (pd.DataFrame, pd.Series)): return make_json_serializable(obj.to_dict())
    elif isinstance(obj, float) and (pd.isna(obj) or obj != obj): return 0.0
    return obj

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            TerminalDashboard.active_states = local_active_states
            TerminalDashboard.global_market_context = local_macro_context
            
            # Derive PCR from Nifty 50 state if available
            nifty_state = local_active_states.get("NSE_INDEX|Nifty 50", {})
            pcr = nifty_state.get('stock_pcr', local_macro_state.get('pcr', 1.0))
            
            fii_net = local_fii_dii_state.get('fii_net', 0)
            dii_net = local_fii_dii_state.get('dii_net', 0)
            date_str = local_fii_dii_state.get('date', local_macro_state.get('date', 'N/A'))
            
            # Dynamically calculate Market Breadth (A/D Ratio) from the active watchlist
            advances = 0
            declines = 0
            for k, v in local_active_states.items():
                if "Nifty 50" in k: continue
                ltp = v.get("ltp", 0)
                pc = v.get("prev_close", ltp)
                if ltp > pc: advances += 1
                elif ltp < pc: declines += 1
                
            dynamic_ad = advances / declines if declines > 0 else (advances if advances > 0 else 1.0)
            ad_ratio = dynamic_ad
            enriched_states = {}
            for instrument_key, payload in local_active_states.items():
                symbol = instrument_key.split('|')[-1] if '|' in instrument_key else instrument_key
                if symbol == "Nifty 50": continue
                
                payload_copy = dict(payload)
                payload_copy["symbol"] = symbol
                
                # Fetch catalyst from News feed state
                if symbol in local_catalyst_cache:
                    payload_copy["latest_catalyst"] = local_catalyst_cache[symbol]
                
                try:
                    payload_copy["structured_payload"] = ReasoningEngine.build_structured_payload(symbol, payload_copy)
                except Exception as e:
                    logger.error(f"Error building structured payload for {symbol}: {e}")
                    payload_copy["structured_payload"] = dict(payload_copy)
                    
                enriched_states[symbol] = payload_copy

            payload = {
                "global_market_context": local_macro_context,
                "dashboard_intraday_plays": getattr(TerminalDashboard, "dashboard_intraday_plays", None),
                "global_state": enriched_states,
                "macro_state": {"pcr": pcr, "fii_net": fii_net, "dii_net": dii_net, "date": date_str, "ad_ratio": ad_ratio}
            }
            await websocket.send_json(make_json_serializable(payload))
            await asyncio.sleep(0.5)
    except WebSocketDisconnect: pass
    except Exception as e: logger.error(f"WebSocket loop exception: {e}")

class LedgerOpenRequest(BaseModel):
    symbol: str
    direction: str
    entry_price: float
    entry_qty: int
    confidence: int
    reason: str
    target: str | None = None
    stoploss: str | None = None

class LedgerManageRequest(BaseModel):
    symbol: str
    action: str
    reason: str
    target: str | None = None
    stoploss: str | None = None

class LedgerCloseRequest(BaseModel):
    symbol: str
    exit_price: float
    exit_qty: int
    reason: str
    charges: float = 0.0

@app.post("/api/ledger/open")
def open_ledger_trade(req: LedgerOpenRequest):
    manager = HistoryManager()
    trade_id = manager.create_trade(req.symbol, req.direction, req.entry_price, req.entry_qty, req.confidence, req.reason, req.target, req.stoploss)
    return {"status": "success", "trade_id": trade_id}

@app.post("/api/ledger/manage")
def manage_ledger_trade(req: LedgerManageRequest):
    manager = HistoryManager()
    success = manager.add_management_log(req.symbol, req.action, req.reason, req.target, req.stoploss)
    return {"status": "success", "updated": success}

@app.post("/api/ledger/close")
def close_ledger_trade(req: LedgerCloseRequest):
    manager = HistoryManager()
    success = manager.add_exit(req.symbol, req.exit_price, req.exit_qty, req.reason, req.charges)
    return {"status": "success", "updated": success}

async def start_api_server():
    logger.info("Starting Web API Server (Port 8000)...")
    import os, json
    playbook_path = os.path.join("trading_copilot", "playbook_state.json")
    if os.path.exists(playbook_path):
        try:
            with open(playbook_path, "r") as f:
                TerminalDashboard.dashboard_intraday_plays = json.load(f)
            logger.info("Loaded saved playbook state.")
        except Exception as e:
            logger.error(f"Failed to load playbook state: {e}")
            
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="warning", ws_ping_interval=None)
    await uvicorn.Server(config).serve()
