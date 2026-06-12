import asyncio
import pandas as pd
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

import os
# Base directory for data (in trading_copilot/data)
_BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = _BASE_DIR / 'data'
DATA_DIR.mkdir(parents=True, exist_ok=True)

def _load_history_sync(symbol: str, days: int) -> pd.DataFrame:
    """Synchronous file I/O for loading history."""
    file_path = DATA_DIR / f"{symbol}_1D.parquet"
    if not file_path.exists():
        logger.warning(f"Parquet file for {symbol} not found at {file_path}")
        return pd.DataFrame()
        
    try:
        # Load the parquet file using pyarrow engine for columnar efficiency
        df = pd.read_parquet(file_path, engine='pyarrow')
        # Return only the last 'days' rows to save RAM
        return df.tail(days)
    except Exception as e:
        logger.error(f"Error loading {symbol} history: {e}")
        return pd.DataFrame()

async def load_recent_history(symbol: str, days: int = 250) -> pd.DataFrame:
    """
    Module 1: The Bootloader
    Loads the last `days` of history for a given symbol into memory.
    Offloads disk I/O to a separate thread to prevent blocking the async loop.
    """
    return await asyncio.to_thread(_load_history_sync, symbol, days)

def _append_eod_sync(symbol: str, new_daily_dict: dict) -> bool:
    """Synchronous file I/O for appending an EOD candle."""
    file_path = DATA_DIR / f"{symbol}_1D.parquet"
    try:
        if file_path.exists():
            # Load the full parquet file
            df = pd.read_parquet(file_path, engine='pyarrow')
            new_df = pd.DataFrame([new_daily_dict])
            # Append the new row
            # If the dataframe has missing/empty columns, ignore_index=True handles it safely
            df = pd.concat([df, new_df], ignore_index=True)
        else:
            df = pd.DataFrame([new_daily_dict])
            
        # Save/overwrite the Parquet file using pyarrow
        df.to_parquet(file_path, engine='pyarrow')
        return True
    except Exception as e:
        logger.error(f"Error appending EOD candle for {symbol}: {e}")
        return False

async def append_eod_candle(symbol: str, new_daily_dict: dict) -> bool:
    """
    Module 2: The EOD Appender
    Appends a new daily candle to the Parquet file for the given symbol.
    Offloads disk I/O to a separate thread.
    """
    return await asyncio.to_thread(_append_eod_sync, symbol, new_daily_dict)
