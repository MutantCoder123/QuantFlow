import json
import urllib.request
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), 'trading_copilot'))
from intraday_gatekeeper import IntradayGatekeeper
from reasoning_engine import ReasoningEngine

try:
    req = urllib.request.Request('http://127.0.0.1:8001/state')
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
        states = data.get('active_states', {})
        for sym, state_str in list(states.items())[:5]:
            if 'active' in sym: continue
            
            state = state_str if isinstance(state_str, dict) else json.loads(state_str)
            # Manually build the payload as the engine does
            struct = ReasoningEngine.build_structured_payload(sym, state, None, None)
            # Evaluate using Gatekeeper
            res = IntradayGatekeeper.evaluate(sym, struct, None)
            
            print(f'=== {sym} ===')
            print(res)
except Exception as e:
    print(e)
