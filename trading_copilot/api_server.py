import os
import asyncio
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse
import uvicorn

from diagnostic_ui import TerminalDashboard
from derivatives_engine import OptionsAnalyzer
from macro_eod_engine import InstitutionalFlowTracker
from config import load_watchlist_from_csv
from screener_engine import PreMarketScreener
import scrip_master_engine

logger = logging.getLogger(__name__)

from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="AlgoTrade Live Web Portal")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load watchlist for token-to-symbol mapping
watchlist_path = os.path.join(os.path.dirname(__file__), "watchlist.csv")
watchlist = load_watchlist_from_csv(watchlist_path)

def make_json_serializable(obj):
    """
    Recursively scans and converts NumPy, Pandas, and non-standard float/int datatypes
    into standard JSON-serializable Python objects to avoid serialization failures.
    """
    import numpy as np
    import pandas as pd

    if isinstance(obj, dict):
        return {str(k): make_json_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple, set)):
        return [make_json_serializable(v) for v in obj]
    elif isinstance(obj, (np.integer, np.int64, np.int32, np.int16, np.int8)):
        return int(obj)
    elif isinstance(obj, (np.floating, np.float64, np.float32, np.float16)):
        if np.isnan(obj) or np.isinf(obj):
            return 0.0
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return make_json_serializable(obj.tolist())
    elif isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    elif isinstance(obj, pd.DataFrame) or isinstance(obj, pd.Series):
        return make_json_serializable(obj.to_dict())
    elif isinstance(obj, float) and (pd.isna(obj) or obj != obj):  # handles NaN/Inf
        return 0.0
    else:
        return obj

@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    template_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            html_content = f.read()
        return HTMLResponse(content=html_content)
    except FileNotFoundError:
        return HTMLResponse(
            content="<h1>templates/index.html not found. Ensure the templates directory exists in trading_copilot/</h1>", 
            status_code=404
        )

@app.post("/api/run-screener")
async def run_screener(request: Request):
    smart_connect = request.app.state.smart_connect
    screener = PreMarketScreener(smart_connect)
    top_picks = await screener.run_scan()
    return {"status": "success", "data": top_picks}

@app.post("/api/map-option-tokens")
async def map_option_tokens(request: Request):
    logger.info("Received request to map option tokens.")
    
    # Download the master if needed
    await scrip_master_engine.download_scrip_master()
    
    option_tokens = []
    # Loop through watchlist equities and fetch ATM option tokens
    for token, meta in watchlist.items():
        symbol = meta.get('symbol', 'UNKNOWN')
        # Grab LTP from the active dashboard state
        state = TerminalDashboard.active_states.get(token, {})
        ltp = state.get('ltp', 0.0)
        
        if ltp > 0:
            base_symbol = symbol.split('-')[0]
            tokens = await scrip_master_engine.get_atm_option_tokens(base_symbol, ltp)
            if tokens:
                option_tokens.extend([tokens.get("CE"), tokens.get("PE")])
                
    # Filter out empty/mocked failures
    option_tokens = [t for t in option_tokens if t]
    
    # Inject them into the websocket_engine's subscription loop
    if hasattr(request.app.state, 'stream_manager') and option_tokens:
        try:
            token_list = [{"exchangeType": 2, "tokens": option_tokens}]
            request.app.state.stream_manager.sws.subscribe(
                correlation_id="stream_options",
                mode=3,
                token_list=token_list
            )
            logger.info(f"Subscribed {len(option_tokens)} new Option tokens to WebSocket.")
        except Exception as e:
            logger.error(f"Failed to subscribe option tokens: {e}")
            
    logger.info(f"Mapped {len(option_tokens)} new Option tokens for the data stream.")
    
    return {"status": "success", "message": "Option tokens mapped and injected successfully."}

# --- Phase 8: Neuro-Symbolic Reasoning Endpoints ---
from pydantic import BaseModel
from reasoning_engine import ReasoningEngine

class InstantAnalyzeRequest(BaseModel):
    model: str = "gemini-3-flash"
    prompt: str = ""
    user_position: dict = None


class LoopStartRequest(BaseModel):
    symbol: str
    interval: int = 90
    model: str = "gemini-3-flash"
    prompt: str = ""
    user_position: dict = None


class LoopStopRequest(BaseModel):
    symbol: str

@app.post("/api/reasoning/instant/{symbol}")
async def instant_analyze(symbol: str, req: InstantAnalyzeRequest):
    logger.info(f"Received instant analysis request for {symbol} using {req.model}")
    report = await ReasoningEngine.analyze_stock(symbol, req.model, req.prompt, user_position=req.user_position)
    return {"status": "success", "report": report}

@app.post("/api/reasoning/loop/start")
async def start_analysis_loop(req: LoopStartRequest):
    logger.info(f"Received loop start request for {req.symbol} every {req.interval}s")
    await ReasoningEngine.start_analysis_loop(req.symbol, req.interval, req.model, req.prompt, user_position=req.user_position)
    return {"status": "success", "message": f"Loop started for {req.symbol}"}

@app.post("/api/reasoning/loop/stop")
async def stop_analysis_loop(req: LoopStopRequest):
    logger.info(f"Received loop stop request for {req.symbol}")
    await ReasoningEngine.stop_analysis_loop(req.symbol)
    return {"status": "success", "message": f"Loop stopped for {req.symbol}"}

@app.get("/api/reasoning/report/{symbol}")
async def get_latest_report(symbol: str):
    report = ReasoningEngine.latest_reports.get(symbol, "No report generated yet.")
    is_active = symbol in ReasoningEngine.active_loops
    return {"status": "success", "report": report, "is_active": is_active}

# --- Phase 9: Alerting Matrix Endpoints ---
@app.get("/api/alerts/unread")
async def get_unread_alerts():
    unread = [a for a in ReasoningEngine.global_alerts if not a["read"]]
    return {"status": "success", "alerts": unread, "count": len(unread)}

@app.get("/api/alerts/history")
async def get_alert_history():
    return {"status": "success", "alerts": ReasoningEngine.global_alerts}

@app.post("/api/alerts/mark-read/{alert_id}")
async def mark_alert_read(alert_id: int):
    for a in ReasoningEngine.global_alerts:
        if a["id"] == alert_id:
            a["read"] = True
            return {"status": "success"}
    return {"status": "error", "message": "Alert not found"}

# --- Phase 10: Dynamic Watchlist API ---
import csv

@app.get("/api/watchlist")
async def get_watchlist():
    return {"status": "success", "data": [{"token": k, "symbol": v["symbol"], "exchange": v["exchange"]} for k, v in watchlist.items()]}

class WatchlistUpdateRequest(BaseModel):
    items: list[dict]

@app.post("/api/watchlist")
async def update_watchlist(req: WatchlistUpdateRequest, request: Request):
    global watchlist
    old_tokens = set(watchlist.keys())
    
    # Save to CSV
    try:
        with open(watchlist_path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Token", "Symbol", "Exchange"])
            for item in req.items:
                writer.writerow([item["token"], item["symbol"], item["exchange"]])
    except Exception as e:
        logger.error(f"Failed to write watchlist.csv: {e}")
        return {"status": "error", "message": str(e)}
        
    # Reload watchlist variable
    from config import load_watchlist_from_csv
    watchlist = load_watchlist_from_csv(watchlist_path)
    
    new_tokens = set(watchlist.keys())
    
    tokens_to_add = new_tokens - old_tokens
    tokens_to_remove = old_tokens - new_tokens
    
    # Handle LiveStreamManager updates
    if hasattr(request.app.state, 'stream_manager') and request.app.state.stream_manager:
        sm = request.app.state.stream_manager
        sm.watchlist = watchlist  # update internal ref
        
        try:
            # Unsubscribe removed
            if tokens_to_remove:
                rm_list = [{"exchangeType": 1, "tokens": list(tokens_to_remove)}] # Assuming NSE (1). You'd map dynamically if multiple.
                sm.sws.unsubscribe("stream_multi", 3, rm_list)
                logger.info(f"Unsubscribed from tokens: {tokens_to_remove}")
            
            # Subscribe new
            if tokens_to_add:
                add_list = [{"exchangeType": 1, "tokens": list(tokens_to_add)}]
                sm.sws.subscribe("stream_multi", 3, add_list)
                logger.info(f"Subscribed to new tokens: {tokens_to_add}")
                
        except Exception as e:
            logger.error(f"Failed dynamic websocket update: {e}")

    return {"status": "success", "message": "Watchlist updated."}

@app.get("/api/search-token")
async def search_token_api(q: str):
    if len(q) < 2: return {"status": "success", "data": []}
    results = await scrip_master_engine.search_scrip_tokens(q)
    return {"status": "success", "data": results}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("New web client connected to Live Matrix WebSocket.")
    try:
        while True:
            # Safely fetch active states
            pcr = OptionsAnalyzer.macro_state.get('pcr', 1.0)
            fii_dii_state = InstitutionalFlowTracker.load_state()
            fii_net = fii_dii_state.get('fii_net', 0)
            dii_net = fii_dii_state.get('dii_net', 0)
            date_str = fii_dii_state.get('date', 'N/A')
            
            ad_ratio = fii_dii_state.get('ad_ratio', 1.0)
            
            # Enrich global states with symbol names
            enriched_states = {}
            for token, payload in TerminalDashboard.active_states.items():
                payload_copy = dict(payload)
                sym_info = watchlist.get(token, {})
                payload_copy["symbol"] = sym_info.get("symbol", f"Token {token}")
                enriched_states[token] = payload_copy

            payload = {
                "global_state": enriched_states,
                "macro_state": {
                    "pcr": pcr,
                    "fii_net": fii_net,
                    "dii_net": dii_net,
                    "date": date_str,
                    "ad_ratio": ad_ratio
                }
            }
            
            serializable_payload = make_json_serializable(payload)
            await websocket.send_json(serializable_payload)
            await asyncio.sleep(0.5)
            
    except WebSocketDisconnect:
        logger.info("Web client disconnected from Live Matrix WebSocket.")
    except Exception as e:
        logger.error(f"WebSocket loop exception: {e}")

async def start_api_server(smart_connect, stream_manager=None):
    app.state.smart_connect = smart_connect
    app.state.stream_manager = stream_manager
    logger.info("Starting FastAPI/Uvicorn server asynchronously...")
    config = uvicorn.Config(app, host="0.0.0.0", port=8000, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()
