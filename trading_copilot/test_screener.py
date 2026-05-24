import asyncio
import os
import logging
from config import load_watchlist_from_csv, API_KEY, CLIENT_CODE, MPIN, TOTP_SECRET
from auth_manager import AngelAuthenticator
from screener_engine import PreMarketScreener

logging.basicConfig(level=logging.DEBUG)

async def test_screener():
    print("\n--- Diagnostic: Phase 0 Screener ---")
    auth = AngelAuthenticator(API_KEY, CLIENT_CODE, MPIN, TOTP_SECRET)
    session = auth.generate_session()
    smart_connect = session.get("smart_connect")
    
    if not smart_connect:
        print("Failed to authenticate.")
        return
        
    screener = PreMarketScreener(smart_connect)
    
    print("Testing run_scan()...")
    results = await screener.run_scan()
    print(f"Final Results: {results}")

if __name__ == "__main__":
    asyncio.run(test_screener())
