import urllib.request, json
try:
    req = urllib.request.Request('http://127.0.0.1:8001/state')
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
        states = data.get('active_states', {})
        print(f'Total symbols: {len(states)}')
        for sym, state in list(states.items())[:5]:
            micro = state.get('structured_payload', {}).get('1_live_microstructure', {}).get('order_flow', {})
            print(f'{sym} : Vol_Z={micro.get("vol_z_score_5m")} REGIME={micro.get("flow_regime")}')
except Exception as e:
    print(e)
