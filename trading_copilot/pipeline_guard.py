import datetime
from zoneinfo import ZoneInfo
import logging

logger = logging.getLogger(__name__)

# Set this to False to enable offline testing mode.
PRODUCTION_LIVE = True

def is_market_open() -> bool:
    """
    Evaluates if the current local time is within the Indian Standard Time (IST) 
    market boundaries: Monday to Friday, 09:15 AM to 03:30 PM.
    """
    if not PRODUCTION_LIVE:
        return True

    # Get current time in IST
    ist_zone = ZoneInfo("Asia/Kolkata")
    now = datetime.datetime.now(ist_zone)

    # Check day of week (0 = Monday, 4 = Friday)
    if now.weekday() > 4:
        return False

    # Calculate decimal hours for easy comparison
    current_time_decimal = now.hour + now.minute / 60.0
    
    # 09:15 = 9.25
    # 15:30 = 15.5
    if 9.25 <= current_time_decimal < 15.5:
        return True
    
    return False
