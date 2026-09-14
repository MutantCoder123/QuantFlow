import asyncio
import aiohttp
import json
import os
import sys

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BASE_DIR not in sys.path:
    sys.path.append(_BASE_DIR)

import scrip_master_engine

async def test_endpoints():
    token_file = os.path.join(_BASE_DIR, "upstox_token.json")
    try:
        with open(token_file, "r") as f:
            data = json.load(f)
            access_token = data.get("access_token")
    except Exception as e:
        print(f"Token error: {e}")
        return

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }

    ikey = "NSE_EQ|INE002A01018" # RELIANCE
    historical_date = "2024-05-15" # Pick a random past date

    async with aiohttp.ClientSession(headers=headers) as session:
        # 1. Get Expiries
        url = f"https://api.upstox.com/v2/expired-instruments/expiries?instrument_key={ikey}"
        async with session.get(url) as response:
            expiries = await response.json()
            print(f"Expiries Status: {response.status}")
            print(json.dumps(expiries, indent=2)[:500])

if __name__ == "__main__":
    asyncio.run(test_endpoints())
