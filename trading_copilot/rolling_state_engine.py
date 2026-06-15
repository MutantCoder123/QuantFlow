import asyncio
import pandas as pd
import logging
import time
import os
import numpy as np
import json
from technical_engine import MathEngine
from diagnostic_ui import TerminalDashboard
from derivatives_engine import OptionsAnalyzer
from macro_eod_engine import InstitutionalFlowTracker as LegacyTracker
try:
    from data_services.macro_worker import InstitutionalFlowTracker
except ImportError:
    import sys, os
    sys.path.append(os.path.join(os.path.dirname(__file__), 'data_services'))
    from macro_worker import InstitutionalFlowTracker

logger = logging.getLogger(__name__)

class RollingStateEngine:
    live_options_state = {}
    daily_metrics_cache = {}
    
    def __init__(self, dfs_map: dict, watchlist: dict = None):
        self.dfs = dfs_map
        self.watchlist = watchlist or {}
        self.phantom_candles = {}
        
        # Hydration (Anti-Cold Start)
        self.cache_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'cache_state.json')
        hydrated = self._hydrate_from_cache()
        
        if not hydrated:
            # Pre-process all tenant dataframes into static boundaries
            for token, token_data in self.dfs.items():
                for df_key in ['ltf_df', 'htf_df']:
                    df = token_data.get(df_key)
                    if df is not None and not df.empty:
                        df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata').dt.tz_localize(None)
                        df.sort_values('timestamp', inplace=True)
                        df.reset_index(drop=True, inplace=True)
                
                # Initialize empty phantom
                self.phantom_candles[token] = None
            
        self._load_parquet_metrics()

    def save_cache(self):
        try:
            out = {
                'dfs': {},
                'phantom_candles': self.phantom_candles,
                'live_options_state': RollingStateEngine.live_options_state,
                'daily_metrics_cache': RollingStateEngine.daily_metrics_cache
            }
            
            for token, token_data in self.dfs.items():
                out['dfs'][token] = {}
                for df_key in ['ltf_df', 'htf_df']:
                    if df_key in token_data and token_data[df_key] is not None and not token_data[df_key].empty:
                        df_copy = token_data[df_key].copy()
                        if 'timestamp' in df_copy.columns:
                            df_copy['timestamp'] = df_copy['timestamp'].astype(str)
                        out['dfs'][token][df_key] = df_copy.to_dict(orient='records')

            with open(self.cache_file, 'w') as f:
                json.dump(out, f, default=str)
            logger.info(f"State successfully serialized to {self.cache_file}")
        except Exception as e:
            logger.error(f"Failed to save state cache: {e}")

    def _hydrate_from_cache(self) -> bool:
        import time
        if not os.path.exists(self.cache_file):
            return False
            
        try:
            # Check if file is older than 24 hours
            if time.time() - os.path.getmtime(self.cache_file) > 86400:
                logger.info("Cache file is older than 24 hours. Starting cold.")
                return False
                
            with open(self.cache_file, 'r') as f:
                data = json.load(f)
                
            self.phantom_candles = data.get('phantom_candles', {})
            RollingStateEngine.live_options_state = data.get('live_options_state', {})
            RollingStateEngine.daily_metrics_cache = data.get('daily_metrics_cache', {})
            
            for token, token_dfs in data.get('dfs', {}).items():
                if token not in self.dfs:
                    self.dfs[token] = {}
                for df_key, records in token_dfs.items():
                    if records:
                        df = pd.DataFrame(records)
                        if 'timestamp' in df.columns:
                            df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata').dt.tz_localize(None)
                        self.dfs[token][df_key] = df
                        
            logger.info("Hydrated state from cache. Engine Anti-Cold Start successful.")
            return True
        except Exception as e:
            logger.error(f"Failed to hydrate from cache: {e}")
            return False

    def _load_parquet_metrics(self):
        import os
        import pandas as pd
        data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
        
        # Default Nifty Macro state
        self.daily_metrics_cache['Nifty 50'] = {'pcr': 1.0, 'max_pain': 0.0, 'atm_iv': 0.0}
        
        for token, token_data in self.dfs.items():
            parent_symbol = self.watchlist.get(token, {}).get("symbol", "").split('-')[0]
            if not parent_symbol:
                continue
                
            parquet_path = os.path.join(data_dir, f"{parent_symbol}_1D.parquet")
            if os.path.exists(parquet_path):
                try:
                    df = pd.read_parquet(parquet_path)
                    if not df.empty:
                        last_row = df.iloc[-1]
                        self.daily_metrics_cache[parent_symbol] = {
                            'pcr': float(last_row.get('EOD_PCR', 1.0)),
                            'max_pain': float(last_row.get('EOD_MAX_PAIN', 0.0)),
                            'atm_iv': float(last_row.get('EOD_IV', 0.0))
                        }
                except Exception as e:
                    logger.error(f"Failed to load parquet for {parent_symbol}: {e}")

    def process_tick(self, token, timestamp_ms, price, volume, oi, greeks=None, bids=None, asks=None):
        """
        O(1) dictionary update. Called directly by WebSocket callback thread.
        """
        tick_ts = pd.to_datetime(timestamp_ms, unit='ms', utc=True).tz_convert('Asia/Kolkata').tz_localize(None)
        
        # 1. Option Greeks Interception
        if greeks:
            sym = token.split('|')[-1]
            parent_symbol = "".join([c for c in sym if not c.isdigit()]).replace("CE","").replace("PE","")
            opt_type = "CE" if "CE" in sym else "PE"
            
            if parent_symbol not in RollingStateEngine.live_options_state:
                RollingStateEngine.live_options_state[parent_symbol] = {
                    "CE_OI": 0, "PE_OI": 0, "CE_LTP": 0, "PE_LTP": 0, 
                    "stock_pcr": 1.0, "current_iv": 0.0, "max_pain_price": None
                }
            
            state = RollingStateEngine.live_options_state[parent_symbol]
            if opt_type == "CE":
                state['CE_LTP'] = price
                if oi > 0: state['CE_OI'] = oi
            elif opt_type == "PE":
                state['PE_LTP'] = price
                if oi > 0: state['PE_OI'] = oi
                
            ce_oi = state['CE_OI']
            pe_oi = state['PE_OI']
            if ce_oi > 0:
                state['stock_pcr'] = round(pe_oi / ce_oi, 4)
                
            if "iv" in greeks:
                state['current_iv'] = round(float(greeks["iv"]), 4)
                
            return # Options don't need phantom candles right now
            
        # 2. Update Phantom Candle for Equities/Indices
        if token not in self.dfs:
            if token == "NSE_INDEX|Nifty 50":
                nifty_state = TerminalDashboard.active_states.get(token, {})
                nifty_state["ltp"] = price
                TerminalDashboard.active_states[token] = nifty_state
            return
            
        boundary_ts = tick_ts.floor('5min')
        
        phantom = self.phantom_candles.get(token)
        if not phantom or phantom['timestamp'] < boundary_ts:
            # Boundary Cross! Commit old phantom to static DF
            if phantom:
                target_df = self.dfs[token].get('ltf_df')
                if target_df is not None:
                    # Append phantom to dataframe (only happens once every 5 mins!)
                    idx = len(target_df)
                    target_df.loc[idx] = phantom
                    
            # Save old microstructure
            old_micro = phantom.get('microstructure', {}) if phantom else {}
            
            # Initialize new phantom
            phantom = {
                'timestamp': boundary_ts,
                'open': price,
                'high': price,
                'low': price,
                'close': price,
                'volume': volume,
                'oi': oi,
                'microstructure': old_micro
            }
            self.phantom_candles[token] = phantom
        else:
            # Update existing phantom in O(1)
            phantom['high'] = max(phantom['high'], price)
            phantom['low'] = min(phantom['low'], price)
            phantom['close'] = price
            phantom['volume'] += volume
            phantom['oi'] = oi
        
        from microstructure_engine import MicrostructureEngine
        # Always run MicrostructureEngine to update volume and POC
        # Even if bids/asks are empty, volume still needs to be accounted for in CVD and POC
        tick_dict = {
            'token': token,
            'price': price,
            'volume': volume,
            'bids': bids,
            'asks': asks
        }
        
        # We need to maintain the previous OBI if bids/asks are empty in this tick
        old_micro = phantom.get('microstructure', {})
        micro_state = MicrostructureEngine.generate_microstructure_payload(tick_dict)
        
        if not bids or not asks:
            # Carry over last known OBI if this tick was just an LTP/Volume update without orderbook data
            micro_state['obi'] = old_micro.get('obi', 0.0)
            
        phantom['microstructure'] = micro_state

    async def calculate_technicals_loop(self):
        """
        Runs asynchronously every 1.5 seconds.
        Dynamically merges the phantom candle into the DataFrame, calculates RSI/MACD, and updates the UI payload.
        """
        logger.info("Starting Lazy Rolling State Calculator Loop...")
        while True:
            try:
                for token, phantom in list(self.phantom_candles.items()):
                    if not phantom:
                        continue
                        
                    token_data = self.dfs.get(token, {})
                    target_df = token_data.get('ltf_df')
                    if target_df is None or target_df.empty:
                        continue
                        
                    # 1. Phantom Merge (Temporary Concat)
                    phantom_df = pd.DataFrame([phantom])
                    # Ensure timestamp format matches
                    temp_df = pd.concat([target_df, phantom_df], ignore_index=True)
                    
                    # 2. Compute Technicals
                    try:
                        from historical_engine import HistoricalFetcher
                        htf_df = token_data.get('htf_df')
                        if htf_df is None:
                            htf_df = pd.DataFrame()
                        # Preserve derivatives data from background workers
                        existing_state = TerminalDashboard.active_states.get(token, {})
                        
                        prev_close = phantom['close']
                        if htf_df is not None and not htf_df.empty:
                            idx = -2 if len(htf_df) > 1 else -1
                            prev_close = float(htf_df['close'].iloc[idx])
                            
                        final_payload = {
                            "token": token,
                            "ltp": phantom['close'],
                            "prev_close": prev_close,
                            "stock_pcr": existing_state.get("stock_pcr", 1.0),
                            "max_pain_price": existing_state.get("max_pain_price", None),
                            "ivr": existing_state.get("ivr", None),
                            **MathEngine.generate_signal_payload(
                                temp_df, 
                                htf_df=htf_df, 
                                token=token, 
                                index_df=HistoricalFetcher.nifty_baseline_df
                            )
                        }
                    except Exception as e:
                        logger.error(f"MathEngine failed for {token}: {e}")
                        continue
                        
                    import datetime, os, json
                    from reasoning_engine import ReasoningEngine
                    from historical_engine import HistoricalFetcher
                    
                    final_payload['token'] = token
                    final_payload['timestamp'] = int(time.time() * 1000)
                    final_payload['ltp'] = phantom['close']
                    
                    parent_symbol = self.watchlist.get(token, {}).get("symbol", "").split('-')[0]
                    final_payload['symbol'] = parent_symbol
                    
                    ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
                    now_ist = datetime.datetime.now(ist)
                    final_payload['current_time'] = now_ist.strftime("%I:%M %p").lower()
                    
                    if datetime.time(9, 15) <= now_ist.time() <= datetime.time(15, 30):
                        final_payload['market_state'] = "LIVE"
                    else:
                        final_payload['market_state'] = "CLOSED"
                        
                    # Load Macro Baselines
                    baselines_path = os.path.join(os.path.dirname(__file__), 'data', 'macro_baselines.json')
                    baselines = {}
                    if os.path.exists(baselines_path):
                        with open(baselines_path, 'r') as f:
                            baselines = json.load(f)
                    
                    stock_macro = baselines.get(parent_symbol, {})
                    vol_edge = stock_macro.get("volatility_edge_52w", {})
                    opt_pos = stock_macro.get("options_positioning_52w", {})
                    struct_liq = stock_macro.get("structural_liquidity_5y", {})
                    regime_conf = stock_macro.get("regime_confluence_5y", {})
                    
                    final_payload['value_area_high'] = struct_liq.get("value_area_high")
                    final_payload['value_area_low'] = struct_liq.get("value_area_low")
                    final_payload['volume_poc_price'] = struct_liq.get("volume_poc_price")
                    
                    final_payload['iv_percentile_52w'] = vol_edge.get("iv_percentile_52w")
                    atm_iv_live = final_payload.get("atm_iv")
                    hv_20d = vol_edge.get("historical_vol_20d")
                    final_payload['iv_hv_premium_pct'] = (atm_iv_live - hv_20d) if (atm_iv_live is not None and hv_20d is not None) else None
                    
                    final_payload['oi_volume_shock_52w_z'] = opt_pos.get("oi_volume_shock_52w_z")
                    final_payload['pcr_percentile_52w'] = opt_pos.get("pcr_percentile_52w")
                    final_payload['drift_20d_strike_migration'] = opt_pos.get("drift_20d_strike_migration")
                    
                    final_payload['alpha_vs_nifty_5y'] = regime_conf.get("alpha_5y")
                    final_payload['beta_vs_nifty_5y'] = regime_conf.get("beta_5y")
                    
                    mp_price = final_payload.get('max_pain_price')
                    final_payload['max_pain_divergence_pct'] = ((final_payload['ltp'] - mp_price) / mp_price * 100) if mp_price else None
                    
                    micro = phantom.get('microstructure', {})
                    final_payload['obi'] = micro.get('obi', 0.0)
                    final_payload['cvd'] = micro.get('cvd', 0)
                    final_payload['distance_to_poc_pct'] = micro.get('poc_distance_pct', 100)
                    final_payload['session_vwap'] = micro.get('session_vwap', 0.0)
                    final_payload['price_to_vwap_pct'] = micro.get('price_to_vwap_pct', 0.0)
                    final_payload['whale_cvd_live'] = micro.get('whale_cvd_live', 0)
                    final_payload['whale_cvd_ema_1h'] = micro.get('whale_cvd_ema_1h', 0.0)
                    final_payload['whale_cvd_slope'] = micro.get('whale_cvd_slope', 0.0)
                    
                    # 20d and 5d Advanced calculations
                    vp_20d = MathEngine.calc_volume_profile_high_fidelity(temp_df, bins=100)
                    final_payload.update(vp_20d)
                    
                    if htf_df is not None and not htf_df.empty:
                        final_payload['alpha_vs_nifty_5d'] = MathEngine.calc_alpha_5d(htf_df, HistoricalFetcher.nifty_baseline_df)
                    else:
                        final_payload['alpha_vs_nifty_5d'] = 0.0
                    
                    # 3. Inject External States
                    nifty_state = TerminalDashboard.active_states.get('NSE_INDEX|Nifty 50', {})
                    final_payload['macro_pcr'] = nifty_state.get('stock_pcr', 1.0)
                    
                    fii_dii_state = InstitutionalFlowTracker.load_state()
                    final_payload['fii_net_flow'] = fii_dii_state.get('fii_net', 0)
                    
                    advances = 0
                    declines = 0
                    for tk, st in TerminalDashboard.active_states.items():
                        if st.get('ltp', 0) > st.get('prev_close', 0): advances += 1
                        elif st.get('ltp', 0) < st.get('prev_close', 0): declines += 1
                    ad_ratio = round(advances / max(declines, 1), 2)
                    final_payload['market_breadth_ad'] = ad_ratio
                    
                    # News & Setup array standardization
                    from news_engine import NewsEngine
                    cat = NewsEngine.catalyst_cache.get(parent_symbol, {})
                    final_payload['raw_news'] = cat.get('raw_news', [])
                    
                    # User Context & Global Market Context
                    final_payload['global_market_context'] = getattr(TerminalDashboard, "global_market_context", None)
                    u_pos = ReasoningEngine.user_positions.get(token) or ReasoningEngine.user_positions.get(parent_symbol)
                    u_ctx = {}
                    if u_pos:
                        u_ctx['position'] = u_pos
                        if 'intent' in u_pos:
                            u_ctx['intent'] = u_pos['intent']
                    final_payload['user_context'] = u_ctx
                    
                    # Confluence Checks
                    vol_z = final_payload.get('vol_z_score_5m', 0)
                    cvd = final_payload.get('cvd', 0)
                    obi = final_payload.get('obi', 0.0)
                    geo = final_payload.get('geometry', {})
                    cdl = final_payload.get('candlesticks', {})
                    poc_dist = final_payload.get('distance_to_poc_pct', 100)
                    
                    has_bullish_cdl = any(v == "Bullish" for k, v in cdl.items() if k != "active_patterns")
                    max_pain = final_payload.get('max_pain_price')
                    has_max_pain_support = False
                    if max_pain and final_payload['ltp'] < max_pain * 0.98 and (has_bullish_cdl or geo.get('double_bottom', False)):
                        has_max_pain_support = True
                    
                    final_payload['high_probability_setup'] = False
                    if (obi > 0.60 and geo.get('double_bottom', False)) or \
                       (vol_z > 2.5 and cvd < -10000) or \
                       (abs(poc_dist) < 0.1 and geo.get('double_bottom', False) and obi > 0.40) or \
                       (final_payload['macro_pcr'] > 1.3 and geo.get('double_bottom', False)) or \
                       (geo.get('double_bottom', False) and final_payload['fii_net_flow'] > 1500) or \
                       has_max_pain_support:
                        final_payload['high_probability_setup'] = True

                    final_payload['prev_close'] = prev_close
                    # 4. Update Terminal State Dict
                    TerminalDashboard.update_state(token, final_payload)
                    
            except Exception as e:
                logger.error(f"Error in lazy calculator loop: {e}")
            
            await asyncio.sleep(1.5)
