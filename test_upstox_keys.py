import asyncio, aiohttp, sys
sys.path.append('trading_copilot/scripts')
import master_bootstrap

async def test():
    try:
        async with aiohttp.ClientSession() as s:
            res = await master_bootstrap.fetch_with_rate_limit(
                s, 
                'https://api.upstox.com/v2/expired-instruments/expiries', 
                {'Accept': 'application/json'}, 
                master_bootstrap.RateLimiter(), 
                params={'instrument_key': 'NSE_EQ|INE009A01021'}
            )
            print('INE009A01021 res:', res)
            
            res2 = await master_bootstrap.fetch_with_rate_limit(
                s, 
                'https://api.upstox.com/v2/expired-instruments/expiries', 
                {'Accept': 'application/json'}, 
                master_bootstrap.RateLimiter(), 
                params={'instrument_key': 'NSE_EQ|INFY'}
            )
            print('INFY res:', res2)
    except Exception as e:
        print('ERR:', e)
asyncio.run(test())
