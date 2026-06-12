import asyncio
import websockets
import json

async def main():
    async with websockets.connect('ws://127.0.0.1:8000/ws') as ws:
        msg = await ws.recv()
        data = json.loads(msg)
        plays = data.get('dashboard_intraday_plays')
        if plays:
            with open('trading_copilot/playbook_state.json', 'w') as f:
                json.dump(plays, f, indent=2)
            print('Saved playbook')
        else:
            print('No playbook found')

asyncio.run(main())
