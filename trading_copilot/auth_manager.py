import pyotp
import logging
from SmartApi import SmartConnect

logger = logging.getLogger(__name__)

class AngelAuthenticator:
    def __init__(self, api_key: str, client_code: str, mpin: str, totp_secret: str):
        self.api_key = api_key
        self.client_code = client_code
        self.mpin = mpin
        self.totp_secret = totp_secret

    def generate_session(self) -> dict:
        try:
            logger.info("Initializing SmartConnect session...")
            smart_connect = SmartConnect(api_key=self.api_key)
            
            totp = pyotp.TOTP(self.totp_secret)
            current_totp = totp.now()
            
            session_data = smart_connect.generateSession(
                clientCode=self.client_code,
                password=self.mpin,
                totp=current_totp
            )
            
            if not session_data or not session_data.get("status"):
                raise ValueError(f"Handshake failed: {session_data}")
                
            data = session_data.get("data", {})
            jwt_token = data.get("jwtToken")
            feed_token = data.get("feedToken") or smart_connect.getFeedToken()
            
            if not jwt_token or not feed_token:
                raise ValueError("Failed to extract tokens from session data.")
                
            logger.info("Session established successfully.")
            return {
                "jwtToken": jwt_token,
                "feedToken": feed_token,
                "smart_connect": smart_connect
            }
        except Exception as e:
            logger.error(f"Error during authentication: {e}")
            raise
