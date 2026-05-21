import asyncio
import pandas as pd
import logging
from technical_engine import MathEngine

logger = logging.getLogger(__name__)

class DataFrameMutator:
    def __init__(self, dfs_map: dict, queue: asyncio.Queue, max_rows: int = 200):
        self.dfs = dfs_map
        self.queue = queue
        self.max_rows = max_rows
        
        # Pre-process all tenant dataframes
        for token, df in self.dfs.items():
            if not df.empty:
                df['timestamp'] = pd.to_datetime(df['timestamp'])
                df.sort_values('timestamp', inplace=True)
                df.reset_index(drop=True, inplace=True)

    async def process_queue(self):
        logger.info("Starting Multi-tenant State Router consumer...")
        while True:
            try:
                tick = await self.queue.get()
                
                tick_token = tick['token']
                # Convert epoch milliseconds to pandas datetime
                tick_ts = pd.to_datetime(tick['timestamp'], unit='ms', utc=True).tz_convert('Asia/Kolkata').tz_localize(None)
                price = tick['price']
                vol = tick['volume']
                
                if tick_token not in self.dfs:
                    self.queue.task_done()
                    continue
                    
                target_df = self.dfs[tick_token]
                
                if target_df.empty:
                    # Initialize first candle if warmup failed
                    new_ts = tick_ts.floor('5min')
                    target_df = pd.DataFrame([{
                        'timestamp': new_ts, 'open': price, 'high': price, 'low': price, 'close': price, 'volume': vol
                    }])
                    self.dfs[tick_token] = target_df
                    self.queue.task_done()
                    continue
                
                # Boundary evaluation
                last_ts = target_df['timestamp'].iloc[-1]
                boundary_end = last_ts + pd.Timedelta(minutes=5)
                
                if tick_ts < boundary_end:
                    # Within boundary: Route to memory dictionary
                    idx = len(target_df) - 1
                    target_df.at[idx, 'close'] = price
                    target_df.at[idx, 'high'] = max(target_df.at[idx, 'high'], price)
                    target_df.at[idx, 'low'] = min(target_df.at[idx, 'low'], price)
                    target_df.at[idx, 'volume'] += vol
                else:
                    # New boundary
                    logger.info(f"Token {tick_token}: New 5-min candle crossed. LTP: {price}")
                    new_ts = tick_ts.floor('5min')
                    new_row = pd.DataFrame([{
                        'timestamp': new_ts, 'open': price, 'high': price, 'low': price, 'close': price, 'volume': vol
                    }])
                    
                    target_df = pd.concat([target_df, new_row], ignore_index=True)
                    
                    # Strictly cap to max_rows per token
                    if len(target_df) > self.max_rows:
                        target_df = target_df.iloc[-self.max_rows:].reset_index(drop=True)
                        
                    self.dfs[tick_token] = target_df
                        
                # Trigger math engine calculations in a background thread
                signal_payload = await asyncio.to_thread(MathEngine.generate_signal_payload, target_df.copy(), tick_token)
                
                # Output the calculated payload for verification
                logger.info(f"[MATH ENGINE] Token: {tick_token} Payload: {signal_payload}")
                
                # Signal Filter Alert
                vol_z = signal_payload.get('vol_z_score', 0)
                cmf = signal_payload.get('cmf', 0)
                rsi = signal_payload.get('rsi', 100)
                
                if vol_z > 2.5 or (cmf > 0.20 and rsi < 30):
                    logger.warning(f"[INSTITUTIONAL ALERT] Token: {tick_token} | High volume/cmf divergence detected! Payload: {signal_payload}")
                    
                # Geometric Alert
                geo = signal_payload.get('geometry', {})
                if geo.get('double_bottom') or geo.get('double_top') or geo.get('head_and_shoulders'):
                    logger.critical(f"[MACRO-GEOMETRY ALERT] Token: {tick_token} | Pattern confirmed! Payload: {signal_payload}")
                
                self.queue.task_done()
            except Exception as e:
                logger.error(f"Error mutating state for token: {e}")
