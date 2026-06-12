import json
import urllib.request
try:
    req = urllib.request.Request('http://127.0.0.1:8001/state')
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
        states = data.get('active_states', {})
        for sym, state in list(states.items())[:2]:
            print(f'=== {sym} ===')
            struct = state.get('structured_payload', {})
            print(json.dumps(struct, indent=2))
except Exception as e:
    print(e)
