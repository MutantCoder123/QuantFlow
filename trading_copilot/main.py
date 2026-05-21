import asyncio
import logging
import logzero

logzero.loglevel(logging.INFO)

from config import API_KEY, CLIENT_CODE, MPIN, TOTP_SECRET, load_watchlist_from_csv
from auth_manager import AngelAuthenticator
from historical_engine import HistoricalFetcher
from websocket_engine import LiveStreamManager
from state_mutator import DataFrameMutator

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
        mutator = DataFrameMutator(warmup_dfs_map, queue)
        
        logger.info("Multi-tenant engines loaded. Starting concurrent routing...")
        
        # 5. Async Concurrency Execution
        await asyncio.gather(
            stream_manager.start_stream(),
            mutator.process_queue()
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
    try:
        asyncio.run(run_system())
    except KeyboardInterrupt:
        print("Terminated by user.")
