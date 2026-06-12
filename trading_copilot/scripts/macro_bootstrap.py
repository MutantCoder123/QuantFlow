import os
import json
import math
import numpy as np
import pandas as pd
from scipy.stats import percentileofscore

def _sanitize_for_json(obj):
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_sanitize_for_json(v) for v in obj]
    elif isinstance(obj, float):
        if math.isnan(obj) or np.isnan(obj) or math.isinf(obj):
            return None
        return round(obj, 4)
    elif pd.isna(obj):
        return None
    return obj

def get_tiered_step_size(median_price):
    if median_price <= 500:
        return 0.50
    elif median_price <= 2000:
        return 1.00
    else:
        return 5.00

def process_file(filepath, nifty_df=None):
    try:
        df = pd.read_parquet(filepath)
    except Exception as e:
        print(f"Error loading {filepath}: {e}")
        return None

    if df.empty:
        return None

    # Sanitize EOD metrics
    for col in ['EOD_IV', 'EOD_PCR', 'EOD_MAX_PAIN']:
        if col in df:
            df[col] = df[col].replace(0.0, np.nan)
            
    df.ffill(inplace=True)
    df.bfill(inplace=True)
    
    metrics = {}

    # 1. 52-Week Derivatives Matrix (Last 252 Days)
    df_252 = df.tail(252).copy()
    if not df_252.empty:
        # IV Percentile and Absolute Bounds
        if 'EOD_IV' in df_252 and len(df_252['EOD_IV'].dropna()) > 0:
            iv_series = df_252['EOD_IV'].dropna()
            current_iv = iv_series.iloc[-1]
            iv_pct = percentileofscore(iv_series, current_iv, kind='weak')
            iv_high = iv_series.max()
            iv_low = iv_series.min()
        else:
            iv_pct = np.nan
            iv_high = np.nan
            iv_low = np.nan
            
        # PCR Percentile
        current_pcr = df_252['EOD_PCR'].iloc[-1] if 'EOD_PCR' in df_252 else np.nan
        if pd.notna(current_pcr) and len(df_252['EOD_PCR'].dropna()) > 0:
            pcr_pct = percentileofscore(df_252['EOD_PCR'].dropna(), current_pcr, kind='weak')
        else:
            pcr_pct = np.nan
            
        # OI Shock Z-Score
        if 'Total_OI' in df_252 and len(df_252['Total_OI'].dropna()) > 1:
            current_oi = df_252['Total_OI'].iloc[-1]
            oi_mean = df_252['Total_OI'].mean()
            oi_std = df_252['Total_OI'].std()
            if oi_std > 0:
                oi_z = (current_oi - oi_mean) / oi_std
            else:
                oi_z = 0.0
        else:
            oi_z = np.nan
            
        # 20-Day Drift in Strike Migration (Max Pain)
        if 'EOD_MAX_PAIN' in df_252 and len(df_252['EOD_MAX_PAIN'].dropna()) >= 20:
            mp_series = df_252['EOD_MAX_PAIN'].dropna()
            current_mp = mp_series.iloc[-1]
            past_mp = mp_series.iloc[-20]
            if past_mp > 0:
                drift_20d = ((current_mp - past_mp) / past_mp) * 100
            else:
                drift_20d = np.nan
        else:
            drift_20d = np.nan
            
        metrics['volatility_edge_52w'] = {
            'iv_percentile_52w': iv_pct,
            'iv_52w_high': iv_high,
            'iv_52w_low': iv_low
        }
        metrics['options_positioning_52w'] = {
            'pcr_percentile_52w': pcr_pct,
            'oi_volume_shock_52w_z': oi_z,
            'drift_20d_strike_migration': drift_20d
        }
    else:
        metrics['volatility_edge_52w'] = {'iv_percentile_52w': np.nan, 'iv_52w_high': np.nan, 'iv_52w_low': np.nan}
        metrics['options_positioning_52w'] = {'pcr_percentile_52w': np.nan, 'oi_volume_shock_52w_z': np.nan, 'drift_20d_strike_migration': np.nan}

    # 2. IV-HV Premium (Last 20 Days)
    df_20 = df.tail(20).copy()
    if not df_20.empty and len(df_20) > 1 and 'Close' in df_20:
        log_rets = np.log(df_20['Close'] / df_20['Close'].shift(1)).dropna()
        if len(log_rets) > 0:
            hv_20d = log_rets.std() * np.sqrt(252) * 100
        else:
            hv_20d = np.nan
    else:
        hv_20d = np.nan
        
    metrics['volatility_edge_52w']['historical_vol_20d'] = hv_20d

    # 3. 5-Year Structural Liquidity
    if 'Close' in df and 'Volume' in df and not df.empty:
        median_price = df['Close'].median()
        step_size = get_tiered_step_size(median_price)
        
        # Round close to nearest step_size
        df['price_bin'] = (df['Close'] / step_size).round() * step_size
        
        # Group by price bin and sum volume
        vol_profile = df.groupby('price_bin')['Volume'].sum().reset_index()
        vol_profile = vol_profile.sort_values(by='Volume', ascending=False).reset_index(drop=True)
        
        total_vol = vol_profile['Volume'].sum()
        
        if total_vol > 0 and len(vol_profile) > 0:
            poc_row = vol_profile.iloc[0]
            poc_price = poc_row['price_bin']
            
            accumulated_vol = 0
            target_vol = total_vol * 0.70
            
            value_area_bins = []
            
            for _, row in vol_profile.iterrows():
                accumulated_vol += row['Volume']
                value_area_bins.append(row['price_bin'])
                if accumulated_vol >= target_vol:
                    break
                    
            val_low = min(value_area_bins)
            val_high = max(value_area_bins)
            
            metrics['structural_liquidity_5y'] = {
                'volume_poc_price': poc_price,
                'value_area_high': val_high,
                'value_area_low': val_low,
                'step_size_used': step_size
            }
        else:
            metrics['structural_liquidity_5y'] = {
                'volume_poc_price': np.nan, 'value_area_high': np.nan, 'value_area_low': np.nan, 'step_size_used': step_size
            }
    else:
        metrics['structural_liquidity_5y'] = {
            'volume_poc_price': np.nan, 'value_area_high': np.nan, 'value_area_low': np.nan, 'step_size_used': np.nan
        }

    # 4. Regime Confluence & Trend (5-Year)
    regime = {
        'macro_trend_alignment': None,
        'beta_5y': np.nan,
        'alpha_5y': np.nan
    }
    
    if 'Close' in df and len(df) >= 200:
        sma_50 = df['Close'].rolling(window=50).mean().iloc[-1]
        sma_200 = df['Close'].rolling(window=200).mean().iloc[-1]
        ltp = df['Close'].iloc[-1]
        
        if pd.notna(sma_50) and pd.notna(sma_200) and pd.notna(ltp):
            if ltp > sma_50 and sma_50 > sma_200:
                regime['macro_trend_alignment'] = "SECULAR_BULL_MARKET_ABOVE_KEY_MA"
            elif ltp < sma_50 and sma_50 < sma_200:
                regime['macro_trend_alignment'] = "SECULAR_BEAR_MARKET_BELOW_KEY_MA"
            else:
                regime['macro_trend_alignment'] = "MACRO_CONSOLIDATION_CHOP"

    # CAPM Alpha and Beta
    if nifty_df is not None and 'Close' in df and 'Close' in nifty_df:
        try:
            # Normalize dates to align perfectly
            df_temp = df[['Date', 'Close']].copy() if 'Date' in df else df[['Close']].copy()
            if 'Date' in df_temp:
                df_temp['Date'] = pd.to_datetime(df_temp['Date']).dt.normalize()
                df_temp.set_index('Date', inplace=True)
                
            nifty_temp = nifty_df[['Date', 'Close']].copy() if 'Date' in nifty_df else nifty_df[['Close']].copy()
            if 'Date' in nifty_temp:
                nifty_temp['Date'] = pd.to_datetime(nifty_temp['Date']).dt.normalize()
                nifty_temp.set_index('Date', inplace=True)
            
            # Inner join to perfectly align the dates
            aligned = df_temp.join(nifty_temp, lsuffix='_stock', rsuffix='_nifty', how='inner')
            
            if len(aligned) > 10:
                s_rets = aligned['Close_stock'].pct_change().dropna()
                n_rets = aligned['Close_nifty'].pct_change().dropna()

                if len(s_rets) > 0:
                    cov_matrix = np.cov(s_rets, n_rets)
                    beta = cov_matrix[0, 1] / cov_matrix[1, 1]
                    
                    stock_ann_ret = s_rets.mean() * 252
                    nifty_ann_ret = n_rets.mean() * 252
                    risk_free_rate = 0.070  # 7.0% RFR for India
                    
                    alpha_simplified = stock_ann_ret - (risk_free_rate + beta * nifty_ann_ret)
                    
                    regime['beta_5y'] = beta
                    regime['alpha_5y'] = alpha_simplified
        except Exception as e:
            print(f"Alpha/Beta Error: {e}")
            pass

    metrics['regime_confluence_5y'] = regime

    return _sanitize_for_json(metrics)

def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(base_dir, 'data')
    output_path = os.path.join(data_dir, 'macro_baselines.json')
    
    if not os.path.exists(data_dir):
        print(f"Data directory {data_dir} not found.")
        return
        
    baselines = {}
    
    nifty_df = None
    nifty_path = os.path.join(data_dir, 'NIFTY50_1D.parquet')
    if os.path.exists(nifty_path):
        nifty_df = pd.read_parquet(nifty_path)
    else:
        print("[WARNING] NIFTY50_1D.parquet not found. Beta and Alpha will be output as null.")
    
    for filename in os.listdir(data_dir):
        if filename.endswith('_1D.parquet') and 'NIFTY' not in filename.upper():
            symbol = filename.replace('_1D.parquet', '')
            filepath = os.path.join(data_dir, filename)
            print(f"Processing {symbol}...")
            metrics = process_file(filepath, nifty_df=nifty_df)
            if metrics:
                baselines[symbol] = metrics
                
    with open(output_path, 'w') as f:
        json.dump(baselines, f, indent=2)
        
    print(f"Successfully generated {output_path} for {len(baselines)} symbols.")

if __name__ == '__main__':
    main()
