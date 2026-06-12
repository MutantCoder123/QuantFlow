import asyncio
import websockets
import json

async def test():
    async with websockets.connect('ws://127.0.0.1:8000/ws') as ws:
        msg = await ws.recv()
        data = json.loads(msg)
        print('Keys in data:', list(data.keys()))
        if 'dashboard_intraday_plays' in data:
            plays = data['dashboard_intraday_plays']
            if plays is None:
                print('plays is null')
            else:
                print('plays keys:', list(plays.keys()) if isinstance(plays, dict) else type(plays))
                if 'watchlist' in plays:
                    print('watchlist len:', len(plays['watchlist']))
        else:
            print('no dashboard_intraday_plays in payload')

asyncio.run(test())
