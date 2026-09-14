import asyncio
import aiohttp
import json

async def test():
    with open('upstox_token.json') as f:
        t = json.load(f)['access_token']
    
    headers = {'Authorization': f'Bearer {t}', 'Accept': 'application/json'}
    url = 'https://api.upstox.com/v2/expired-instruments/historical-candle/NSE_FO%7C41014/day/2024-05-30/2024-05-01'
    
    async with aiohttp.ClientSession(headers=headers) as s:
        async with s.get(url) as r:
            text = await r.text()
            print(f"Status: {r.status} -> {text[:200]}")

asyncio.run(test())
