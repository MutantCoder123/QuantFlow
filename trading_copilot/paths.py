"""Single source of truth for every filesystem path in the project.

Never compute a path with ``os.path.dirname(__file__)`` chains again. Two such
chains previously resolved to ``AlgoTrade/data`` -- a directory that does not
exist -- which silently disabled parquet metric loading in RollingStateEngine
and the IV-Rank history lookup in derivatives_worker. Both failed through
``os.path.exists()`` checks, so nothing ever logged an error.
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent            # .../AlgoTrade/trading_copilot
REPO_ROOT = BASE_DIR.parent                           # .../AlgoTrade

DATA_DIR = BASE_DIR / "data"
SIGNALS_DIR = DATA_DIR / "signals"
TICKS_DIR = DATA_DIR / "ticks"
FEATURES_DIR = DATA_DIR / "features"
CACHE_DIR = BASE_DIR / "cache"
CONFIG_DIR = BASE_DIR / "config"
LOGS_DIR = BASE_DIR / "logs"

WATCHLIST_PATH = BASE_DIR / "watchlist.csv"
TOKEN_PATH = BASE_DIR / "upstox_token.json"
PLAYBOOK_PATH = BASE_DIR / "playbook_state.json"

MACRO_BASELINES_PATH = DATA_DIR / "macro_baselines.json"
INSTITUTIONAL_FLOW_PATH = DATA_DIR / "institutional_flow.json"
CACHE_STATE_PATH = DATA_DIR / "cache_state.json"
TRADE_HISTORY_PATH = DATA_DIR / "trade_history.json"

SCRIP_MASTER_PATH = CACHE_DIR / "UpstoxMaster.csv.gz"

_WRITABLE = (DATA_DIR, SIGNALS_DIR, TICKS_DIR, FEATURES_DIR, CACHE_DIR, CONFIG_DIR, LOGS_DIR)


def ensure_dirs() -> None:
    """Create every directory the system writes to. Safe to call repeatedly."""
    for d in _WRITABLE:
        d.mkdir(parents=True, exist_ok=True)


def parquet_path(symbol: str) -> Path:
    """Daily OHLCV + EOD derivatives parquet for a symbol."""
    return DATA_DIR / f"{symbol}_1D.parquet"
