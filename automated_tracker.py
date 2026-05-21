"""
Angel One SmartAPI (V2 Endpoints) - Automated Market Tracking Layer
===================================================================

This script provides the core infrastructure for an automated market tracking layer.
It is built with an asynchronous architecture using Python's `asyncio` to keep
the main process responsive while executing blocking API actions.

Architecture:
- STEP 1: Programmatic session connection handshake via 2FA (TOTP) and SmartConnect.
- STEP 2: Historical OHLCV data warming, formatting, and conversion to a Pandas DataFrame.
- STEP 3: Real-time tick streaming using the modern SmartWebSocketV2 client in a dedicated daemon thread.
- EXECUTION CORE: Orchestrates all components sequentially and maintains persistent streaming.

Dependencies:
    pip install smartapi-python pyotp pandas logzero websocket-client

Author: Antigravity
Date: May 2026
"""

import asyncio
import json
import threading
from datetime import datetime, timedelta
import pandas as pd
import pyotp
from logzero import logger
from SmartApi import SmartConnect
from SmartApi.smartWebSocketV2 import SmartWebSocketV2

import os
from dotenv import load_dotenv, find_dotenv

# Load configuration from .env file
load_dotenv(find_dotenv())

# ==========================================
# CONFIGURATION & PLACEHOLDERS
# ==========================================
# Loaded dynamically from the .env file
API_KEY = os.getenv("API_KEY")
CLIENT_CODE = os.getenv("CLIENT_CODE")
PASSWORD_OR_MPIN = os.getenv("MPIN")
TOTP_SECRET_KEY = os.getenv("TOTP_SECRET")

# Asset tracking config
SYMBOL_TOKEN = "3045"       # Numeric ID assigned by the exchange (e.g., '3045' for SBIN-EQ on NSE)
EXCHANGE_TYPE = 1           # 1 represents NSE_CM (Cash Market)
INTERVAL = "FIVE_MINUTE"    # Target historical interval (ONE_MINUTE, FIVE_MINUTE, ONE_DAY, etc.)
DAYS_BACK = 5               # Number of days of historical data to fetch on warmup

# Unique 10-character alphanumeric request identifier for WebSocket subscriptions
CORRELATION_ID = "mktrk10001"

# Global placeholder for the WebSocket stream client
sws = None


# ==============================================================================
# STEP 1: THE SESSION HANDSHAKE GATEWAY
# ==============================================================================
def establish_session(api_key: str, client_code: str, password: str, totp_secret: str) -> tuple[SmartConnect, str, str]:
    """
    Initializes the SmartConnect client, generates a dynamic 2FA TOTP token,
    and opens a secure session handshake with Angel One.

    Returns:
        tuple: (smart_connect_instance, jwt_token, feed_token)
    """
    logger.info("Initializing session connection handshake...")
    
    # 1. Initialize SmartConnect object using the explicit api_key
    smart_connect = SmartConnect(api_key=api_key)
    
    # 2. Implement programmatic two-factor authentication (2FA)
    # Generates a dynamic 6-digit TOTP string using pyotp and the totp_secret_key
    try:
        totp = pyotp.TOTP(totp_secret)
        current_totp_token = totp.now()
        logger.info(f"Generated dynamic TOTP token: {current_totp_token[:2]}****")
    except Exception as e:
        logger.error(f"Failed to generate TOTP token: {e}")
        raise ValueError("Invalid TOTP secret key or pyotp error") from e

    # 3. Call generateSession to complete the handshake
    try:
        session_data = smart_connect.generateSession(
            clientCode=client_code,
            password=password,
            totp=current_totp_token
        )
        
        # Verify that session generation succeeded
        if not session_data or not session_data.get("status"):
            error_msg = session_data.get("message") if session_data else "No response"
            error_code = session_data.get("errorcode") if session_data else ""
            raise ValueError(f"Handshake failed: {error_msg} (Error Code: {error_code})")
        
        logger.info("Session handshake established successfully.")
    except Exception as e:
        logger.error(f"Error during API generateSession call: {e}")
        raise

    # 4. Extract and print out tokens explicitly
    data = session_data.get("data", {})
    jwt_token = data.get("jwtToken")
    feed_token = data.get("feedToken")
    
    if not jwt_token or not feed_token:
        # Fallback to getFeedToken if not found in dictionary
        feed_token = feed_token or smart_connect.getFeedToken()
        
    if not jwt_token or not feed_token:
        raise ValueError("Failed to extract Auth Tokens (jwtToken or feedToken) from response data.")

    # Explicitly print credentials to console as requested
    print("\n" + "=" * 60)
    print("STEP 1 GATEWAY CONNECTIVITY ESTABLISHED")
    print(f"jwtToken (Auth Token): {jwt_token[:15]}...[TRUNCATED]")
    print(f"feedToken (Feed Token): {feed_token[:15]}...[TRUNCATED]")
    print("=" * 60 + "\n")

    return smart_connect, jwt_token, feed_token


# ==============================================================================
# STEP 2: HISTORICAL OHLCV WARMUP ENGINE
# ==============================================================================
def fetch_historical_data(smart_connect_instance: SmartConnect, symbol_token: str, interval: str, days_back: int = 5) -> pd.DataFrame:
    """
    Requests historical candle data, converts it into a structured DataFrame,
    and returns the warmed data matrix.
    
    Args:
        smart_connect_instance: Warmed-up authenticated SmartConnect session.
        symbol_token: Instrument numeric token.
        interval: Historical interval (e.g., 'FIVE_MINUTE').
        days_back: Number of days back to start historical records.
    """
    logger.info(f"Warming up historical data engine for Token {symbol_token}...")
    
    # 1. Enforce strict interval and casing constraints (Must be uppercase string representation)
    interval_upper = str(interval).upper()
    
    # 2. Compute date range - formatting constraint: Time strings must match "YYYY-MM-DD HH:MM"
    to_date_obj = datetime.now()
    from_date_obj = to_date_obj - timedelta(days=days_back)
    
    from_date_str = from_date_obj.strftime("%Y-%m-%d %H:%M")
    to_date_str = to_date_obj.strftime("%Y-%m-%d %H:%M")
    
    logger.info(f"Target interval: {interval_upper} | Range: {from_date_str} to {to_date_str}")
    
    # Define historical parameters dict
    historic_param = {
        "exchange": "NSE",
        "symboltoken": symbol_token,
        "interval": interval_upper,
        "fromdate": from_date_str,
        "todate": to_date_str
    }
    
    # 3. Call historical endpoint
    try:
        response = smart_connect_instance.getCandleData(historic_param)
        if not response or not response.get("status"):
            error_msg = response.get("message") if response else "No response"
            error_code = response.get("errorcode") if response else ""
            raise ValueError(f"Historical query failed: {error_msg} (Error Code: {error_code})")
        
        candles = response.get("data")
        if candles is None:
            logger.warning("Historical endpoint returned empty candle dataset.")
            candles = []
            
    except Exception as e:
        logger.error(f"Error fetching candle data: {e}")
        raise

    # 4. Convert nested JSON array directly into a structured Pandas DataFrame with explicit column names
    columns = ['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume']
    df = pd.DataFrame(candles, columns=columns)
    
    # Format timestamps cleanly for aesthetics
    if not df.empty:
        df['Timestamp'] = pd.to_datetime(df['Timestamp'])
        df['Timestamp'] = df['Timestamp'].dt.strftime('%Y-%m-%d %H:%M')

    # 5. Print a clear confirmation message showcasing the top 3 rows and overall row count
    print("\n" + "=" * 60)
    print("STEP 2 HISTORICAL OHLCV WARMUP ENGINE COMPLETE")
    print(f"Data Matrix Populated: {len(df)} rows")
    print("Top 3 Rows:")
    print(df.head(3).to_string(index=False))
    print("=" * 60 + "\n")
    
    return df


# ==============================================================================
# STEP 3: LIVE STREAM STREAMING LAYER (SmartWebSocketV2)
# ==============================================================================
def setup_live_stream(jwt_token: str, api_key: str, client_code: str, feed_token: str, symbol_token: str):
    """
    Instantiates and establishes Angel One's streaming client.
    Handles background connection and callback hooking.
    """
    global sws
    logger.info("Initializing modern streaming client layer...")
    
    # 1. Instantiate Angel One's modern streaming client using SmartWebSocketV2
    sws = SmartWebSocketV2(
        auth_token=jwt_token,
        api_key=api_key,
        client_code=client_code,
        feed_token=feed_token
    )

    # 2. Implement explicit callback hooks
    
    def on_open(wsapp):
        """
        Callback triggered on websocket connection.
        Subscribes to full Snap Quote feeds.
        """
        logger.info("[STREAM] WebSocket Connection Established.")
        
        # Subscribe to full Snap Quote feeds (mode = 3)
        # exchangeType: 1 -> NSE Cash Market
        subscription_list = [{"exchangeType": EXCHANGE_TYPE, "tokens": [symbol_token]}]
        try:
            logger.info(f"[STREAM] Subscribing to token {symbol_token} (Snap Quote, Mode 3)...")
            sws.subscribe(
                correlation_id=CORRELATION_ID,
                mode=3,
                token_list=subscription_list
            )
            logger.info("[STREAM] Subscription command dispatched successfully.")
        except Exception as e:
            logger.error(f"[STREAM] Subscription failed during handshake: {e}")

    def on_data(wsapp, message):
        """
        Callback triggered on every transaction tick.
        Parses JSON payload, extracts LTP, scales it to Rupee value and prints it.
        """
        try:
            # Parse tick payload
            if isinstance(message, (str, bytes)):
                data = json.loads(message)
            else:
                data = message
            
            logger.debug(f"[STREAM] Raw packet: {data}")
            
            # SmartWebSocketV2 data contains a list of tick objects or a single dict
            ticks = data if isinstance(data, list) else [data]
            
            for tick in ticks:
                # Retrieve price and token metadata
                ltp_raw = tick.get("last_traded_price") or tick.get("ltp") or tick.get("lp")
                token = tick.get("token") or tick.get("token_id")
                
                # Filter valid price ticks
                if ltp_raw is not None:
                    # Angel One transfers prices scaled as integers (multiplied by 100)
                    ltp_rupees = float(ltp_raw) / 100.0
                    
                    # Print the clean rupee value to terminal
                    print(f">>> [TICK ALERT] Token: {token} | LTP: ₹{ltp_rupees:.2f}")
                else:
                    logger.debug(f"[STREAM] Metadata/Handshake packet: {tick}")
                    
        except Exception as e:
            logger.error(f"[STREAM] Error parsing tick frame: {e}")

    def on_error(wsapp, error):
        """
        Callback triggered on transmission error.
        """
        logger.error(f"[STREAM] Connection error occurred: {error}")

    def on_close(wsapp):
        """
        Callback triggered upon socket closure.
        """
        logger.warn("[STREAM] WebSocket Connection Terminated Cleanly.")

    # 3. Assign hooks to the websocket manager
    sws.on_open = on_open
    sws.on_data = on_data
    sws.on_error = on_error
    sws.on_close = on_close

    # 4. Spin up connection thread
    # SmartWebSocketV2.connect() is synchronous and blocking, so we run it in a daemon thread
    def connection_runner():
        try:
            logger.info("[STREAM] Launching connect loop in thread...")
            sws.connect()
        except Exception as e:
            logger.critical(f"[STREAM] WebSocket crash: {e}")

    ws_thread = threading.Thread(target=connection_runner, daemon=True)
    ws_thread.start()
    logger.info("[STREAM] Streaming thread started in background.")


# ==============================================================================
# EXECUTION CORE ARCHITECTURE
# ==============================================================================
async def main():
    """
    Asynchronous main entry orchestrator.
    Sequentially links step 1, 2, and 3, and ensures the script keeps
    streaming alive.
    """
    logger.info("Initializing Asynchronous Market Tracker Main Thread...")
    
    # -------------------------------------------------------------
    # STEP 1: THE SESSION HANDSHAKE GATEWAY
    # -------------------------------------------------------------
    # Execute synchronous handshake in an executor thread to preserve async loops
    try:
        smart_connect, jwt_token, feed_token = await asyncio.to_thread(
            establish_session, API_KEY, CLIENT_CODE, PASSWORD_OR_MPIN, TOTP_SECRET_KEY
        )
    except Exception as e:
        logger.critical(f"FATAL: Step 1 Session Handshake failed: {e}")
        logger.info("Please ensure you replaced the credentials at the top of this script.")
        return

    # -------------------------------------------------------------
    # STEP 2: HISTORICAL OHLCV WARMUP ENGINE
    # -------------------------------------------------------------
    # Fetch historical warmup data safely
    try:
        _ = await asyncio.to_thread(
            fetch_historical_data, smart_connect, SYMBOL_TOKEN, INTERVAL, DAYS_BACK
        )
    except Exception as e:
        logger.error(f"NON-FATAL: Step 2 Historical Warmup failed: {e}")
        logger.warn("Continuing setup to initialize live streaming feed...")

    # -------------------------------------------------------------
    # STEP 3: LIVE STREAM STREAMING LAYER
    # -------------------------------------------------------------
    # Launch WebSocket client
    try:
        setup_live_stream(jwt_token, API_KEY, CLIENT_CODE, feed_token, SYMBOL_TOKEN)
    except Exception as e:
        logger.critical(f"FATAL: Step 3 live stream setup failed: {e}")
        return

    # -------------------------------------------------------------
    # EVENT LOOP PERSISTENCE
    # -------------------------------------------------------------
    # Ensure that the live WebSocket feed remains open and actively streams ticks
    # without causing the main process to exit prematurely.
    logger.info("Market Tracking Layer fully activated. Streaming tick messages...")
    logger.info("Press Ctrl+C to terminate.")
    
    keep_alive = asyncio.Event()
    try:
        # Await indefinitely until KeyboardInterrupt triggers
        await keep_alive.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Interruption signal captured. Terminating and closing WebSocket connections...")
        # Clean shutdown if supported by websocket client
        if sws:
            try:
                sws.close_connection()
            except Exception as e:
                logger.debug(f"Failed to close websocket explicitly: {e}")
        logger.info("System shut down cleanly.")


if __name__ == "__main__":
    # Standard entry point execution context
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Program execution terminated.")
