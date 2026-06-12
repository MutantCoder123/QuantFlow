import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO, format='[%(levelname)s %(asctime)s %(module)s:%(lineno)d] %(message)s')

from api_server import start_api_server

async def run_system():
    logger = logging.getLogger(__name__)
    logger.info("Initializing Web UI Process...")
    
    try:
        await start_api_server()
        
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Interruption signal captured. Shutting down Web Server...")
        import os
        os._exit(0)
    except Exception as e:
        logger.critical(f"Fatal error encountered: {e}")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(run_system())
    except KeyboardInterrupt:
        import os
        os._exit(0)
