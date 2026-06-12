import asyncio, websockets, json
async def test():
    async with websockets.connect('ws://localhost:8000/ws') as ws:
        msg = await ws.recv()
        data = json.loads(msg)
        global_state = data['global_state']
        
        print(f"{'SYMBOL':<15} | {'VOL_Z':<8} | {'VWAP_PCT':<8} | {'POC_DIST':<8} | {'KD':<25}")
        print('-'*70)
        
        for sym, sail in global_state.items():
            struct = sail.get('structured_payload', sail)
            micro = struct.get('1_live_microstructure', {}).get('order_flow', {})
            macro = struct.get('3_macro_statistical_edge_5y', {}).get('structural_liquidity', {})
            
            vol_z_score_5m = float(micro.get('vol_z_score_5m', 0.0))
            distance_to_poc_pct = float(macro.get('distance_to_poc_pct', 100.0))
            price_to_vwap_pct = float(sail.get('price_to_vwap_pct', micro.get('price_to_vwap_pct', 100.0)))
            
            kd_obj = struct.get('1_live_microstructure', {}).get('mtf_technicals', {}).get('kinetic_divergence', {})
            kd = kd_obj.get('divergence_state', '') if isinstance(kd_obj, dict) else (kd_obj if isinstance(kd_obj, str) else '')
            
            print(f"{sym:<15} | {vol_z_score_5m:<8.2f} | {price_to_vwap_pct:<8.2f} | {distance_to_poc_pct:<8.2f} | {kd[:25]:<25}")

asyncio.run(test())
