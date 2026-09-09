import os
import sys
import asyncio
import logging
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

# Append parent directory to path so we can import from trading_copilot
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(level=logging.INFO, format='[%(levelname)s %(asctime)s %(module)s:%(lineno)d] %(message)s')
logger = logging.getLogger(__name__)

from config import API_KEY, CLIENT_CODE, MPIN, TOTP_SECRET, load_watchlist_from_csv
from auth_manager import AngelAuthenticator
from historical_engine import HistoricalFetcher
from websocket_engine import LiveStreamManager
from state_mutator import DataFrameMutator
from diagnostic_ui import TerminalDashboard
import scrip_master_engine

app = FastAPI(title="Smart API Feed Daemon")

smart_connect = None
stream_manager = None
mutator = None
WATCHLIST = {}

class WatchlistUpdateRequest(BaseModel):
    items: list[dict]

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
    elif isinstance(obj, np.ndarray):
        return make_json_serializable(obj.tolist())
    elif isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    elif isinstance(obj, pd.DataFrame) or isinstance(obj, pd.Series):
        return make_json_serializable(obj.to_dict())
    elif isinstance(obj, float) and (pd.isna(obj) or obj != obj):
        return 0.0
    return obj

@app.get("/state")
async def get_state():
    payload = {
        "active_states": TerminalDashboard.active_states,
        "watchlist": WATCHLIST
    }
    return make_json_serializable(payload)


@app.post("/api/watchlist/update")
async def update_watchlist_api(req: WatchlistUpdateRequest):
    global WATCHLIST, stream_manager, mutator, smart_connect
    
    old_tokens = set(WATCHLIST.keys())
    new_csv_watchlist = {item["token"]: {"symbol": item["symbol"], "exchange": item["exchange"]} for item in req.items}
    new_tokens = set(new_csv_watchlist.keys())
    tokens_to_add = new_tokens - old_tokens
    tokens_to_remove = old_tokens - new_tokens
    
    options_to_remove = []
    for tk in tokens_to_remove:
        if tk in WATCHLIST: del WATCHLIST[tk]
        if mutator and tk in mutator.dfs: del mutator.dfs[tk]
        if tk in TerminalDashboard.active_states: del TerminalDashboard.active_states[tk]
            
        for opt_tk, opt_meta in list(WATCHLIST.items()):
            if opt_meta.get("parent_token") == tk:
                options_to_remove.append(opt_tk)
                del WATCHLIST[opt_tk]
                if mutator and opt_tk in mutator.dfs: del mutator.dfs[opt_tk]
                if opt_tk in TerminalDashboard.active_states: del TerminalDashboard.active_states[opt_tk]
                    
    if tokens_to_add:
        logger.info(f"[SMART API] Onboarding {len(tokens_to_add)} new tokens...")
        new_sub_dict = {tk: new_csv_watchlist[tk] for tk in tokens_to_add}
        fetcher = HistoricalFetcher()
        new_dfs_map = await fetcher.fetch_batch_warmups(smart_connect, new_sub_dict)
        asyncio.create_task(fetcher.fetch_and_save_parquet_batch(smart_connect, new_sub_dict, 1500))
        
        await scrip_master_engine.download_scrip_master()
        option_tokens_to_add = []
        
        for tk, meta in new_sub_dict.items():
            WATCHLIST[tk] = meta
            if mutator and tk in new_dfs_map:
                mutator.dfs[tk] = new_dfs_map[tk]
                htf_df = new_dfs_map[tk].get('htf_df')
                if htf_df is not None and not htf_df.empty:
                    spot_price = float(htf_df['close'].iloc[-1])
                    symbol = meta.get('symbol', '').split('-')[0]
                    atm_tokens = await scrip_master_engine.get_atm_option_tokens(symbol, spot_price)
                    if atm_tokens:
                        if atm_tokens.get("CE"):
                            WATCHLIST[atm_tokens["CE"]] = {"symbol": f"{symbol}-CE", "exchange": "NFO", "is_option": True, "parent_token": tk}
                            option_tokens_to_add.append(atm_tokens["CE"])
                        if atm_tokens.get("PE"):
                            WATCHLIST[atm_tokens["PE"]] = {"symbol": f"{symbol}-PE", "exchange": "NFO", "is_option": True, "parent_token": tk}
                            option_tokens_to_add.append(atm_tokens["PE"])

        if stream_manager and stream_manager.sws:
            try:
                if tokens_to_remove:
                    stream_manager.sws.unsubscribe("stream_multi", 3, [{"exchangeType": 1, "tokens": list(tokens_to_remove)}])
                if options_to_remove:
                    stream_manager.sws.unsubscribe("stream_options_remove", 3, [{"exchangeType": 2, "tokens": options_to_remove}])
                    
                add_list = [{"exchangeType": 1, "tokens": list(tokens_to_add)}]
                if option_tokens_to_add: add_list.append({"exchangeType": 2, "tokens": option_tokens_to_add})
                stream_manager.sws.subscribe("stream_multi", 3, add_list)
            except Exception as e:
                logger.error(f"[SMART API] Failed websocket update: {e}")
                
    return {"status": "success", "message": "Watchlist updated."}

@app.post("/api/map-option-tokens")
async def map_option_tokens_api():
    await scrip_master_engine.download_scrip_master()
    option_tokens = []
    for token, meta in list(WATCHLIST.items()):
        if meta.get("is_option"): continue
        symbol = meta.get('symbol', 'UNKNOWN')
        ltp = TerminalDashboard.active_states.get(token, {}).get('ltp', 0.0)
        
        if ltp > 0:
            base_symbol = symbol.split('-')[0]
            tokens = await scrip_master_engine.get_atm_option_tokens(base_symbol, ltp)
            if tokens:
                option_tokens.extend([t for t in (tokens.get("CE"), tokens.get("PE")) if t])
                
    if stream_manager and option_tokens:
        try:
            stream_manager.sws.subscribe("stream_options", 3, [{"exchangeType": 2, "tokens": option_tokens}])
            for ot in option_tokens:
                parent_tk = None
                base_symbol = None
                for t, m in WATCHLIST.items():
                    if not m.get("is_option"):
                        bs = m.get('symbol', '').split('-')[0]
                        tokens_check = await scrip_master_engine.get_atm_option_tokens(bs, TerminalDashboard.active_states.get(t, {}).get("ltp", 0.0))
                        if tokens_check and (tokens_check.get("CE") == ot or tokens_check.get("PE") == ot):
                            parent_tk = t
                            base_symbol = bs
                            break
                if parent_tk:
                    is_ce = (tokens_check.get("CE") == ot)
                    suffix = "CE" if is_ce else "PE"
                    WATCHLIST[ot] = {"symbol": f"{base_symbol}-{suffix}", "exchange": "NFO", "is_option": True, "parent_token": parent_tk}
        except Exception as e:
            logger.error(f"[SMART API] Option mapping failed: {e}")
    return {"status": "success"}

async def start_service():
    global smart_connect, stream_manager, mutator, WATCHLIST
    logger.info("Starting Smart API Feed (Port 8001)...")
    
    authenticator = AngelAuthenticator(API_KEY, CLIENT_CODE, MPIN, TOTP_SECRET)
    session_info = await asyncio.to_thread(authenticator.generate_session)
    smart_connect = session_info['smart_connect']
    jwt_token = session_info['jwtToken']
    feed_token = session_info['feedToken']
    
    csv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "watchlist.csv")
    WATCHLIST = load_watchlist_from_csv(csv_path)
    
    fetcher = HistoricalFetcher()
    warmup_dfs_map = await fetcher.fetch_batch_warmups(smart_connect, WATCHLIST)
    
    await scrip_master_engine.download_scrip_master()
    
    for token, meta in list(WATCHLIST.items()):
        symbol = meta.get('symbol', '').split('-')[0]
        htf_df = warmup_dfs_map.get(token, {}).get('htf_df')
        if htf_df is not None and not htf_df.empty:
            spot_price = float(htf_df['close'].iloc[-1])
            TerminalDashboard.active_states[token] = {"ltp": spot_price}
            atm_tokens = await scrip_master_engine.get_atm_option_tokens(symbol, spot_price)
            if atm_tokens:
                if atm_tokens.get("CE"): WATCHLIST[atm_tokens["CE"]] = {"symbol": f"{symbol}-CE", "exchange": "NFO", "is_option": True, "parent_token": token}
                if atm_tokens.get("PE"): WATCHLIST[atm_tokens["PE"]] = {"symbol": f"{symbol}-PE", "exchange": "NFO", "is_option": True, "parent_token": token}
                    
    if fetcher.nifty_baseline_df is not None and not fetcher.nifty_baseline_df.empty:
        TerminalDashboard.active_states["99926000"] = {"ltp": float(fetcher.nifty_baseline_df['close'].iloc[-1])}
                    
    queue = asyncio.Queue()
    stream_manager = LiveStreamManager(jwt_token, API_KEY, CLIENT_CODE, feed_token, WATCHLIST, queue)
    mutator = DataFrameMutator(warmup_dfs_map, queue, watchlist=WATCHLIST)
    
    config = uvicorn.Config(app, host="127.0.0.1", port=8001, log_level="warning")
    server = uvicorn.Server(config)
    
    tasks = [
        asyncio.create_task(stream_manager.start_stream()),
        asyncio.create_task(mutator.process_queue()),
        asyncio.create_task(server.serve())
    ]
    
    try:
        await asyncio.gather(*tasks)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Shutting down Smart API feed...")
        if stream_manager and stream_manager.sws:
            try: stream_manager.sws.close_connection()
            except: pass
        os._exit(0)

if __name__ == "__main__":
    try: asyncio.run(start_service())
    except KeyboardInterrupt: os._exit(0)
