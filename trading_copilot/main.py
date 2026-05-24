import asyncio
import logging

logging.basicConfig(level=logging.INFO, format='[%(levelname)s %(asctime)s %(module)s:%(lineno)d] %(message)s')

from config import API_KEY, CLIENT_CODE, MPIN, TOTP_SECRET, load_watchlist_from_csv
from auth_manager import AngelAuthenticator
from historical_engine import HistoricalFetcher
from websocket_engine import LiveStreamManager
from state_mutator import DataFrameMutator
from diagnostic_ui import TerminalDashboard
from derivatives_engine import OptionsAnalyzer
from macro_eod_engine import InstitutionalFlowTracker
from api_server import start_api_server

async def run_system():
    logger = logging.getLogger(__name__)
    logger.info("Initializing Multi-tenant Watchlist Ingestion Engine...")
    
    try:
        # 1. Authenticaton Gateway
        authenticator = AngelAuthenticator(API_KEY, CLIENT_CODE, MPIN, TOTP_SECRET)
        session_info = await asyncio.to_thread(authenticator.generate_session)
        
        smart_connect = session_info['smart_connect']
        jwt_token = session_info['jwtToken']
        feed_token = session_info['feedToken']
        
        # 1.5 Load Watchlist dynamically
        WATCHLIST = load_watchlist_from_csv("watchlist.csv")
        if not WATCHLIST:
            logger.critical("Watchlist is empty. Exiting...")
            return
            
        # 2. Parallel Historical Data Warmup
        fetcher = HistoricalFetcher()
        warmup_dfs_map = await fetcher.fetch_batch_warmups(smart_connect, WATCHLIST)
        
        # Phase 5.5: ATM Mapping
        import scrip_master_engine
        await scrip_master_engine.download_scrip_master()
        
        for token, meta in list(WATCHLIST.items()):
            symbol = meta.get('symbol', '').split('-')[0]
            dfs = warmup_dfs_map.get(token, {})
            htf_df = dfs.get('htf_df')
            
            if htf_df is not None and not htf_df.empty:
                spot_price = float(htf_df['close'].iloc[-1])
                atm_tokens = await scrip_master_engine.get_atm_option_tokens(symbol, spot_price)
                if atm_tokens:
                    ce_token = atm_tokens.get("CE")
                    pe_token = atm_tokens.get("PE")
                    if ce_token:
                        WATCHLIST[ce_token] = {"symbol": f"{symbol}-CE", "exchange": "NFO", "is_option": True, "parent_token": token}
                    if pe_token:
                        WATCHLIST[pe_token] = {"symbol": f"{symbol}-PE", "exchange": "NFO", "is_option": True, "parent_token": token}
                        
        logger.info(f"Total Watchlist size after Option Injection: {len(WATCHLIST)} tokens.")
        
        # 3. Message Queue Initialization
        queue = asyncio.Queue()
        
        # 4. Stream & Consumer Instantiation
        stream_manager = LiveStreamManager(
            auth_token=jwt_token,
            api_key=API_KEY,
            client_code=CLIENT_CODE,
            feed_token=feed_token,
            watchlist=WATCHLIST,
            queue=queue
        )
        mutator = DataFrameMutator(warmup_dfs_map, queue, watchlist=WATCHLIST)
        
        logger.info("Multi-tenant engines loaded. Starting concurrent routing...")
        
        # 5. Async Concurrency Execution
        dashboard = TerminalDashboard()
        options_analyzer = OptionsAnalyzer()
        flow_tracker = InstitutionalFlowTracker()
        # Phase 7: News Engine
        from news_engine import NewsEngine
        
        await asyncio.gather(
            stream_manager.start_stream(),
            mutator.process_queue(),
            options_analyzer.start_polling(smart_connect, WATCHLIST),
            flow_tracker.start_daily_cron(),
            dashboard.render_loop(),
            start_api_server(smart_connect, stream_manager),
            NewsEngine.start_news_polling(WATCHLIST)
        )
        
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Interruption signal captured. Shutting down system...")
        if 'stream_manager' in locals() and stream_manager.sws:
            try:
                stream_manager.sws.close_connection()
            except Exception as e:
                logger.debug(f"Failed to close websocket explicitly: {e}")
        logger.info("System shut down cleanly.")
    except Exception as e:
        logger.critical(f"Fatal error encountered: {e}")

if __name__ == "__main__":
    import os
    if os.environ.get("ALGO_RELOADER") == "true":
        try:
            asyncio.run(run_system())
        except KeyboardInterrupt:
            pass
    else:
        import sys
        import time
        import subprocess

        print("\nStarting AlgoTrade Engine with Auto-Reload...\n")

        def get_mtimes():
            mtimes = {}
            for root, dirs, files in os.walk(os.path.dirname(__file__) or "."):
                for f in files:
                    if f.endswith('.py') or f.endswith('.html'):
                        fp = os.path.join(root, f)
                        try:
                            mtimes[fp] = os.path.getmtime(fp)
                        except FileNotFoundError:
                            pass
            return mtimes

        mtimes = get_mtimes()

        while True:
            env = os.environ.copy()
            env["ALGO_RELOADER"] = "true"
            process = subprocess.Popen([sys.executable] + sys.argv, env=env)

            changed = False
            try:
                # Poll while the process is actively running
                while process.poll() is None:
                    time.sleep(1)
                    current_mtimes = get_mtimes()
                    for fp, mtime in current_mtimes.items():
                        if fp not in mtimes or mtime > mtimes[fp]:
                            print(f"\n[Auto-Reloader] Modification detected in '{os.path.basename(fp)}'. Restarting engine...\n")
                            mtimes = current_mtimes
                            changed = True
                            break
                    if changed:
                        process.terminate()
                        process.wait()
                        time.sleep(1.5)  # Allow Windows to release TCP port 8000
                        break
            except KeyboardInterrupt:
                process.terminate()
                break

            # If the process crashed (e.g., syntax error) and we didn't force terminate it, wait for a file edit before restarting
            if not changed and process.poll() is not None and process.returncode != 0:
                print("\n[Auto-Reloader] Engine crashed or stopped. Waiting for file edits to restart...\n")
                try:
                    while True:
                        time.sleep(1)
                        current_mtimes = get_mtimes()
                        changed = False
                        for fp, mtime in current_mtimes.items():
                            if fp not in mtimes or mtime > mtimes[fp]:
                                print(f"\n[Auto-Reloader] Modification detected. Attempting to restart...\n")
                                mtimes = current_mtimes
                                changed = True
                                break
                        if changed:
                            break
                except KeyboardInterrupt:
                    break
