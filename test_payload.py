import sys
sys.path.append('trading_copilot')
from reasoning_engine import ReasoningEngine
import time

payload = {
    "ltp": 100.5,
    "timestamp": int(time.time() * 1000),
    "prev_close": 98.0,
    "high_probability_setup": True,
    "cvd": 12000,
    "obi": 0.8,
    "vol_z_score_5m": 3.1,
    "session_vwap": 99.8,
    "price_to_vwap_pct": 0.701,
    "whale_cvd_live": 5000,
    "whale_cvd_ema_1h": 4500.0,
    "whale_cvd_slope": 150.0
}

symbol = "NSE_EQ|RELIANCE"

tactical = ReasoningEngine.build_structured_payload(symbol, payload)
import json
print(json.dumps(tactical, indent=2))
