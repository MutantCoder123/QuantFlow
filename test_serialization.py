import json
import pandas as pd

def serialize_dfs(dfs):
    out = {}
    for token, token_data in dfs.items():
        out[token] = {}
        for df_key in ['ltf_df', 'htf_df']:
            if df_key in token_data and token_data[df_key] is not None:
                # Convert timestamps to string
                df_copy = token_data[df_key].copy()
                if 'timestamp' in df_copy.columns:
                    df_copy['timestamp'] = df_copy['timestamp'].astype(str)
                out[token][df_key] = df_copy.to_dict(orient='records')
    return out

# just a test
