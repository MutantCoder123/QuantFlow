import asyncio
import aiohttp
import json
from datetime import datetime

async def test():
    with open('upstox_token.json') as f:
        t = json.load(f)['access_token']
    
    headers = {'Authorization': f'Bearer {t}', 'Accept': 'application/json'}
    url = 'https://api.upstox.com/v2/market/max-pain'
    
    # Try getting without date first (live)
    p = {
        'instrument_key': 'NSE_INDEX|Nifty 50',
        'expiry': '2026-06-11', # Assuming Thursday expiry near June 5
        'bucket_interval': 60
    }
    
    async with aiohttp.ClientSession(headers=headers) as s:
        async with s.get(url, params=p) as r:
            text = await r.text()
            print(f"Status (No Date): {r.status} -> {text[:200]}")

asyncio.run(test())
