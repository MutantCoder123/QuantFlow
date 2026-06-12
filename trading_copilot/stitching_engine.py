import pandas as pd
import asyncio
from datetime import datetime
import logging
from warm_layer_engine import load_recent_history
import pandas_ta as ta

logger = logging.getLogger(__name__)

def synthesize_live_daily_candle(hot_layer_df_5m: pd.DataFrame) -> dict:
    """
    Module 1: The Synthesizer
    Extracts today's Open, High, Low, Close, and Volume from the 5m dataframe.
    """
    if hot_layer_df_5m is None or hot_layer_df_5m.empty:
        return {}
        
    # Determine today's date to filter the 5m dataframe
    today = pd.Timestamp(datetime.now().date())
    
    # Filter for today's data
    if isinstance(hot_layer_df_5m.index, pd.DatetimeIndex):
        today_df = hot_layer_df_5m[hot_layer_df_5m.index.date == today.date()]
    else:
        time_col = 'timestamp' if 'timestamp' in hot_layer_df_5m.columns else 'date'
        if time_col in hot_layer_df_5m.columns:
            today_df = hot_layer_df_5m[pd.to_datetime(hot_layer_df_5m[time_col]).dt.date == today.date()]
        else:
            today_df = hot_layer_df_5m

    if today_df.empty:
        return {}

    # Map column names dynamically to handle case sensitivity
    col_map = {c.lower(): c for c in today_df.columns}
    o_col = col_map.get('open', 'Open')
    h_col = col_map.get('high', 'High')
    l_col = col_map.get('low', 'Low')
    c_col = col_map.get('close', 'Close')
    v_col = col_map.get('volume', 'Volume')

    try:
        daily_candle = {
            'date': today,
            o_col: float(today_df[o_col].iloc[0]),
            h_col: float(today_df[h_col].max()),
            l_col: float(today_df[l_col].min()),
            c_col: float(today_df[c_col].iloc[-1]),
            v_col: float(today_df[v_col].sum())
        }
        return daily_candle
    except Exception as e:
        logger.error(f"Error synthesizing live candle: {e}")
        return {}

def _calc_indicators_sync(stitched_df: pd.DataFrame) -> dict:
    """
    Synchronous mathematical calculations to be run in a separate thread.
    Uses pandas-ta for indicator computation.
    """
    if stitched_df.empty or len(stitched_df) < 20:
        return {}

    try:
        col_map = {c.lower(): c for c in stitched_df.columns}
        c_col = col_map.get('close', 'Close')
        v_col = col_map.get('volume', 'Volume')

        # Calculate Indicators (append=True adds them directly to the dataframe)
        # RSI (14)
        stitched_df.ta.rsi(close=c_col, length=14, append=True)
        # MACD (12, 26, 9)
        stitched_df.ta.macd(close=c_col, fast=12, slow=26, signal=9, append=True)
        # 200 SMA
        stitched_df.ta.sma(close=c_col, length=200, append=True)
        
        # Calculate Daily POC (Point of Control)
        # As an efficient approximation without full volume profile computation, 
        # we identify the closing price of the day with the absolute highest volume in the history slice.
        poc = stitched_df.loc[stitched_df[v_col].idxmax(), c_col] if v_col in stitched_df.columns else 0.0

        latest = stitched_df.iloc[-1]
        
        # Safely extract dynamic column names created by pandas-ta
        rsi_col = next((c for c in stitched_df.columns if c.startswith('RSI_')), None)
        macd_col = next((c for c in stitched_df.columns if c.startswith('MACD_') and 'MACDs' not in c and 'MACDh' not in c), None)
        sma_col = next((c for c in stitched_df.columns if c.startswith('SMA_200')), None)

        return {
            'daily_rsi': float(latest[rsi_col]) if rsi_col and pd.notna(latest[rsi_col]) else 0.0,
            'daily_macd': float(latest[macd_col]) if macd_col and pd.notna(latest[macd_col]) else 0.0,
            'daily_200_sma': float(latest[sma_col]) if sma_col and pd.notna(latest[sma_col]) else 0.0,
            'daily_poc': float(poc)
        }
    except Exception as e:
        logger.error(f"Error calculating mathematical indicators: {e}")
        return {}

async def calculate_dynamic_daily_indicators(symbol: str, hot_layer_df_5m: pd.DataFrame) -> dict:
    """
    Module 2: The Math Engine
    Orchestrates the retrieval of history, live synthesis, stitching, and multithreaded math.
    """
    # 1. Load historical daily dataframe from Warm Layer (Wait for Disk I/O)
    hist_df = await load_recent_history(symbol, days=250)
    
    # 2. Synthesize today's live candle from the 5-minute Hot Layer
    live_candle = synthesize_live_daily_candle(hot_layer_df_5m)
    
    # 3. The Stitch: Append synthesized live candle to historical data
    if live_candle:
        live_df = pd.DataFrame([live_candle])
        # Ignore index safely stitches dataframes without index collision errors
        stitched_df = pd.concat([hist_df, live_df], ignore_index=True)
    else:
        stitched_df = hist_df

    # 4. Offload heavy mathematical calculations to a separate thread
    indicators = await asyncio.to_thread(_calc_indicators_sync, stitched_df)
    return indicators
