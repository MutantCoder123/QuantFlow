import asyncio
import logging
import json
from SmartApi.smartWebSocketV2 import SmartWebSocketV2

logger = logging.getLogger(__name__)

class LiveStreamManager:
    def __init__(self, auth_token: str, api_key: str, client_code: str, feed_token: str, watchlist: dict, queue: asyncio.Queue):
        self.watchlist = watchlist
        self.queue = queue
        self.sws = SmartWebSocketV2(
            auth_token=auth_token,
            api_key=api_key,
            client_code=client_code,
            feed_token=feed_token
        )
        self.sws.on_open = self.on_open
        self.sws.on_data = self.on_data
        self.sws.on_error = self.on_error
        self.sws.on_close = self.on_close

    def on_open(self, wsapp):
        logger.info("WebSocket connected. Dynamically mapping multi-tenant tokens...")
        
        # Group tokens by exchange for payload creation
        exchange_map = {}
        for token, metadata in self.watchlist.items():
            # 1 for NSE Cash. Extend mapping as needed for MCX, BSE, etc.
            exch_type = 1 if metadata['exchange'] == 'NSE' else 2 
            if exch_type not in exchange_map:
                exchange_map[exch_type] = []
            exchange_map[exch_type].append(token)
            
        # Hardcode NIFTY index (99926000) subscription for Macro PCR engine
        if 1 not in exchange_map:
            exchange_map[1] = []
        if "99926000" not in exchange_map[1]:
            exchange_map[1].append("99926000")
            
        token_list = [{"exchangeType": exch, "tokens": tokens} for exch, tokens in exchange_map.items()]
        
        logger.info(f"Subscribing to: {token_list}")
        self.sws.subscribe(
            correlation_id="stream_multi",
            mode=3,
            token_list=token_list
        )

    def on_data(self, wsapp, message):
        try:
            data = json.loads(message) if isinstance(message, str) else message
            ticks = data if isinstance(data, list) else [data]
            
            for tick in ticks:
                token = tick.get("token")
                ltp_raw = tick.get("last_traded_price")
                vol = tick.get("last_traded_quantity") or 0
                ts = tick.get("exchange_timestamp")
                
                if token and ltp_raw is not None and ts is not None:
                    bids = tick.get("best_5_buy_data") or []
                    asks = tick.get("best_5_sell_data") or []
                    
                    # Extract OI
                    oi = tick.get("open_interest") or 0
                    
                    # Angel One prices in L2 arrays are scaled by 100
                    for b in bids:
                        if 'price' in b:
                            b['price'] = float(b['price']) / 100.0
                    for a in asks:
                        if 'price' in a:
                            a['price'] = float(a['price']) / 100.0
                            
                    # Instantly push multi-tenant packet to queue with asset isolation identifier
                    tick_data = {
                        "token": token,
                        "price": float(ltp_raw) / 100.0,
                        "volume": float(vol),
                        "timestamp": ts,
                        "bids": bids,
                        "asks": asks,
                        "oi": float(oi)
                    }
                    self.queue.put_nowait(tick_data)
        except Exception as e:
            logger.error(f"Error in on_data: {e}")

    def on_error(self, wsapp, error):
        logger.error(f"WebSocket error: {error}")

    def on_close(self, wsapp):
        logger.warning("WebSocket connection closed.")

    async def start_stream(self):
        logger.info("Starting Multi-tenant WebSocket stream...")
        await asyncio.to_thread(self.sws.connect)
