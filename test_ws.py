import asyncio, websockets
import json
async def main():
    async with websockets.connect('ws://127.0.0.1:8000/ws') as ws:
        msg = await ws.recv()
        data = json.loads(msg)
        print("Keys:", data.keys())
        print("dashboard_intraday_plays:", str(data.get('dashboard_intraday_plays'))[:500])
asyncio.run(main())
