import asyncio
import pandas as pd
from state_mutator import DataFrameMutator
from scrip_master_engine import option_map
from diagnostic_ui import TerminalDashboard
import logging

logging.basicConfig(level=logging.INFO)

async def test():
    queue = asyncio.Queue()
    dfs = {
        "123": {
            "ltf_df": pd.DataFrame([{
                "timestamp": pd.Timestamp.now(),
                "open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000
            }]),
            "htf_df": pd.DataFrame([{
                "timestamp": pd.Timestamp.now(),
                "open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000
            }])
        }
    }
    watchlist = {"123": {"symbol": "RELIANCE-EQ", "is_option": False}}
    TerminalDashboard.active_states["123"] = {"ltp": 2500.0}
    
    option_map["456"] = {"parent": "RELIANCE", "type": "CE", "strike": 2500.0}
    option_map["789"] = {"parent": "RELIANCE", "type": "PE", "strike": 2500.0}
    
    mutator = DataFrameMutator(dfs, queue, watchlist, 200)
    
    # Push Option CE Tick
    queue.put_nowait({
        "token": "456",
        "price": 150.0,
        "volume": 500,
        "oi": 10000,
        "timestamp": int(pd.Timestamp.now().timestamp() * 1000)
    })
    
    # Push Option PE Tick
    queue.put_nowait({
        "token": "789",
        "price": 145.0,
        "volume": 500,
        "oi": 12000,
        "timestamp": int(pd.Timestamp.now().timestamp() * 1000)
    })
    
    # Push Equity Tick
    queue.put_nowait({
        "token": "123",
        "price": 2505.0,
        "volume": 500,
        "timestamp": int(pd.Timestamp.now().timestamp() * 1000)
    })
    
    # Stop pill
    queue.put_nowait(None)
    
    await mutator.process_queue()

if __name__ == "__main__":
    asyncio.run(test())
