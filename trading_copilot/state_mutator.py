import asyncio
import pandas as pd
import logging
from technical_engine import MathEngine
from microstructure_engine import MicrostructureEngine
from diagnostic_ui import TerminalDashboard
from derivatives_engine import OptionsAnalyzer
from macro_eod_engine import InstitutionalFlowTracker
from historical_engine import HistoricalFetcher

logger = logging.getLogger(__name__)

class DataFrameMutator:
    live_options_state = {}
    
    def __init__(self, dfs_map: dict, queue: asyncio.Queue, watchlist: dict = None, max_rows: int = 200):
        self.dfs = dfs_map
        self.queue = queue
        self.watchlist = watchlist or {}
        self.max_rows = max_rows
        
        # Pre-process all tenant dataframes
        for token, token_data in self.dfs.items():
            for df_key in ['ltf_df', 'htf_df']:
                df = token_data.get(df_key)
                if df is not None and not df.empty:
                    df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata').dt.tz_localize(None)
                    df.sort_values('timestamp', inplace=True)
                    df.reset_index(drop=True, inplace=True)

    async def process_queue(self):
        logger.info("Starting Multi-tenant State Router consumer...")
        while True:
            try:
                tick = await self.queue.get()
                if tick is None:
                    self.queue.task_done()
                    break
                
                tick_token = tick['token']
                # Convert epoch milliseconds to pandas datetime
                tick_ts = pd.to_datetime(tick['timestamp'], unit='ms', utc=True).tz_convert('Asia/Kolkata').tz_localize(None)
                price = tick['price']
                vol = tick['volume']
                oi = tick.get('oi', 0)
                
                # Phase 5.5: Live Option Tick Interception & Math Execution
                from scrip_master_engine import option_map
                from derivatives_engine import implied_volatility
                
                if tick_token in option_map:
                    meta = option_map[tick_token]
                    parent_token = meta.get("parent") # Actually, it's symbol. Wait, let's use symbol as key or token?
                    parent_symbol = meta.get("parent")
                    opt_type = meta.get("type")
                    strike = meta.get("strike")
                    
                    if parent_symbol not in DataFrameMutator.live_options_state:
                        DataFrameMutator.live_options_state[parent_symbol] = {
                            "CE_OI": 0, "PE_OI": 0, "CE_LTP": 0, "PE_LTP": 0, 
                            "stock_pcr": 1.0, "current_iv": 0.0, "max_pain_price": None
                        }
                    
                    state = DataFrameMutator.live_options_state[parent_symbol]
                    
                    if opt_type == "CE":
                        state['CE_LTP'] = price
                        if oi > 0: state['CE_OI'] = oi
                    elif opt_type == "PE":
                        state['PE_LTP'] = price
                        if oi > 0: state['PE_OI'] = oi
                        
                    # Calculate PCR
                    ce_oi = state['CE_OI']
                    pe_oi = state['PE_OI']
                    if ce_oi > 0:
                        state['stock_pcr'] = round(pe_oi / ce_oi, 4)
                        
                    # Calculate Live IV
                    # Find spot price from TerminalDashboard
                    # We need the parent token to look up the spot price in active_states
                    # We can find parent_token by searching the watchlist for this symbol
                    parent_tok = None
                    for t, m in self.watchlist.items():
                        if m.get('symbol', '').split('-')[0] == parent_symbol and not m.get('is_option'):
                            parent_tok = t
                            break
                            
                    if parent_tok:
                        spot_data = TerminalDashboard.active_states.get(parent_tok, {})
                        spot_price = spot_data.get('ltp', 0.0)
                        
                        if spot_price > 0 and strike > 0 and price > 0:
                            # Hardcoded DTE as 1 day for subsecond approximation
                            t_years = 1.0 / 365.0 
                            r = 0.10
                            flag = 'c' if opt_type == "CE" else 'p'
                            iv = implied_volatility(price, spot_price, strike, t_years, r, flag)
                            if iv > 0:
                                state['current_iv'] = round(iv, 4)
                    
                    self.queue.task_done()
                    continue
                
                if tick_token not in self.dfs:
                    self.queue.task_done()
                    continue
                    
                token_data = self.dfs[tick_token]
                target_df = token_data.get('ltf_df', pd.DataFrame())
                htf_df = token_data.get('htf_df', pd.DataFrame())
                
                if target_df.empty:
                    # Initialize first candle if warmup failed
                    new_ts = tick_ts.floor('5min')
                    target_df = pd.DataFrame([{
                        'timestamp': new_ts, 'open': price, 'high': price, 'low': price, 'close': price, 'volume': vol
                    }])
                    self.dfs[tick_token]['ltf_df'] = target_df
                    target_df = self.dfs[tick_token].get('ltf_df')
                
                htf_df = self.dfs[tick_token].get('htf_df')
                
                # Guard against historical API tarpit failures resulting in NoneType DataFrames
                if target_df is None or target_df.empty or htf_df is None or htf_df.empty:
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
                        
                    self.dfs[tick_token]['ltf_df'] = target_df
                        
                # Phase 3.1: Process Level 2 Microstructure
                micro_payload = MicrostructureEngine.generate_microstructure_payload(tick)
                
                # Trigger math engine calculations in a background thread
                signal_payload = await asyncio.to_thread(MathEngine.generate_signal_payload, target_df.copy(), htf_df.copy(), tick_token, HistoricalFetcher.nifty_baseline_df)
                
                # Merge Phase 2 and Phase 3 payloads
                final_payload = {**signal_payload, **micro_payload}
                final_payload['ltp'] = price
                
                # Phase 4.1: Inject Global Macro State
                final_payload['macro_pcr'] = OptionsAnalyzer.macro_state.get('pcr', 1.0)
                
                # Phase 5.2: Inject Micro Derivatives State
                # parent_symbol lookup
                parent_symbol = self.watchlist.get(tick_token, {}).get("symbol", "").split('-')[0]
                deriv_data = DataFrameMutator.live_options_state.get(parent_symbol, {})
                
                final_payload['stock_pcr'] = deriv_data.get('stock_pcr', 1.0)
                final_payload['max_pain_price'] = None # Hardcoded as per instructions since we only stream ATM
                final_payload['ivr'] = deriv_data.get('current_iv', 0.0)
                
                # Phase 4.2 & 6: Inject Institutional Flow and Market Breadth State
                fii_dii_state = InstitutionalFlowTracker.load_state()
                final_payload['fii_net_flow'] = fii_dii_state.get('fii_net', 0)
                final_payload['market_breadth_ad'] = fii_dii_state.get('ad_ratio', 1.0)
                
                # Phase 7: The High-Frequency Autonomous News Engine
                from news_engine import NewsEngine
                final_payload['latest_catalyst'] = NewsEngine.catalyst_cache.get(parent_symbol, "NEUTRAL - Monitoring tape for live catalysts.")
                
                # Update UI Dashboard state without blocking
                TerminalDashboard.update_state(tick_token, final_payload)
                
                # Phase 3 Confluence Engine
                vol_z = final_payload.get('vol_z_score', 0)
                cmf = final_payload.get('cmf', 0)
                rsi = final_payload.get('rsi', 100)
                geo = final_payload.get('geometry', {})
                cdl = final_payload.get('candlesticks', {})
                obi = final_payload.get('obi', 0.0)
                cvd = final_payload.get('cvd', 0)
                poc_dist = final_payload.get('poc_distance_pct', 100)
                
                # Check for any Bullish candlestick
                has_bullish_cdl = any(v == "Bullish" for k, v in cdl.items() if k != "active_patterns")
                
                # Phase 5.2: Max Pain Support Detection
                max_pain = final_payload.get('max_pain_price')
                has_max_pain_support = False
                if max_pain and price < max_pain * 0.98 and (has_bullish_cdl or geo.get('double_bottom', False)):
                    has_max_pain_support = True
                
                if (obi > 0.60 and geo.get('double_bottom', False)) or \
                   (vol_z > 2.5 and cvd < -10000) or \
                   (abs(poc_dist) < 0.1 and geo.get('double_bottom', False) and obi > 0.40) or \
                   (final_payload['macro_pcr'] > 1.3 and geo.get('double_bottom', False)) or \
                   (geo.get('double_bottom', False) and final_payload['fii_net_flow'] > 1500) or \
                   has_max_pain_support:
                    logger.critical(f"[CONFLUENCE ALERT] Token: {tick_token} | High-Probability Setup Detected!")
                
                self.queue.task_done()
            except Exception as e:
                import traceback
                traceback.print_exc()
                logger.error(f"Error mutating state for token {tick_token if 'tick_token' in locals() else 'unknown'}: {e}")
                self.queue.task_done()
