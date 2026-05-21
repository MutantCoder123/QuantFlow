import os
from dotenv import load_dotenv, find_dotenv

# Load configuration from .env file
load_dotenv(find_dotenv())

API_KEY = os.getenv("API_KEY")
CLIENT_CODE = os.getenv("CLIENT_CODE")
MPIN = os.getenv("MPIN")
TOTP_SECRET = os.getenv("TOTP_SECRET")

import csv
import logging

logger = logging.getLogger(__name__)

def load_watchlist_from_csv(filepath="watchlist.csv"):
    watchlist = {}
    try:
        with open(filepath, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                token = str(row.get("Token", "")).strip()
                if token:
                    watchlist[token] = {
                        "symbol": str(row.get("Symbol", "")).strip(),
                        "exchange": str(row.get("Exchange", "")).strip()
                    }
        logger.info(f"Loaded {len(watchlist)} tokens from {filepath}")
    except FileNotFoundError:
        logger.critical(f"Watchlist file {filepath} not found.")
    except Exception as e:
        logger.critical(f"Error reading watchlist {filepath}: {e}")
    return watchlist
