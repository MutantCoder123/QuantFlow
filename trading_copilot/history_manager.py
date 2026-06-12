import os
import json
import time
import threading
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class HistoryManager:
    _instance = None
    _lock = threading.Lock()
    _history_file = os.path.join(os.path.dirname(__file__), "data", "trade_history.json")

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(HistoryManager, cls).__new__(cls)
                cls._instance._ensure_file()
        return cls._instance

    def _ensure_file(self):
        os.makedirs(os.path.dirname(self._history_file), exist_ok=True)
        if not os.path.exists(self._history_file):
            with open(self._history_file, 'w') as f:
                json.dump({}, f)

    def _read_data(self):
        try:
            with open(self._history_file, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error reading trade history: {e}")
            return {}

    def _write_data(self, data):
        try:
            with open(self._history_file, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logger.error(f"Error writing trade history: {e}")

    def create_trade(self, symbol: str, direction: str, entry_price: float, entry_qty: int, confidence: int, reason: str, target: str = None, stoploss: str = None):
        with self._lock:
            data = self._read_data()
            if symbol not in data:
                data[symbol] = []
                
            ts = int(time.time())
            trade_id = f"TRD-{symbol}-{ts}"
            
            new_trade = {
                "trade_id": trade_id,
                "symbol": symbol,
                "direction": direction,
                "status": "OPEN",
                "realized_pnl": 0.0,
                "entry": {
                    "timestamp": ts,
                    "datetime": datetime.now().isoformat(),
                    "executed_price": entry_price,
                    "executed_quantity": entry_qty,
                    "llm_confidence": confidence,
                    "llm_reason": reason,
                    "llm_target": target,
                    "llm_stoploss": stoploss
                },
                "management_log": [],
                "exits": []
            }
            data[symbol].append(new_trade)
            self._write_data(data)
            return trade_id

    def add_management_log(self, symbol: str, action: str, reason: str, target: str = None, stoploss: str = None):
        with self._lock:
            data = self._read_data()
            trades = data.get(symbol, [])
            
            # Find OPEN trade
            open_trade = next((t for t in reversed(trades) if t.get("status") == "OPEN"), None)
            if open_trade:
                log_entry = {
                    "timestamp": int(time.time()),
                    "datetime": datetime.now().isoformat(),
                    "action": action,
                    "llm_reason": reason
                }
                if target:
                    log_entry["llm_target"] = target
                if stoploss:
                    log_entry["llm_stoploss"] = stoploss
                open_trade["management_log"].append(log_entry)
                self._write_data(data)
                return True
            return False

    def add_exit(self, symbol: str, exit_price: float, exit_qty: int, reason: str, charges: float = 0.0):
        with self._lock:
            data = self._read_data()
            trades = data.get(symbol, [])
            
            open_trade = next((t for t in reversed(trades) if t.get("status") == "OPEN"), None)
            if not open_trade:
                return False
                
            open_trade["exits"].append({
                "timestamp": int(time.time()),
                "datetime": datetime.now().isoformat(),
                "executed_price": exit_price,
                "executed_quantity": exit_qty,
                "llm_reason": reason,
                "charges": charges
            })
            
            total_exited_qty = sum(ex["executed_quantity"] for ex in open_trade["exits"])
            entry_qty = open_trade["entry"]["executed_quantity"]
            entry_price = open_trade["entry"]["executed_price"]
            
            # Calculate running PnL
            total_pnl = 0.0
            direction = open_trade["direction"]
            total_charges = 0.0
            for ex in open_trade["exits"]:
                if direction.lower() == "long":
                    total_pnl += (ex["executed_price"] - entry_price) * ex["executed_quantity"]
                else:
                    total_pnl += (entry_price - ex["executed_price"]) * ex["executed_quantity"]
                total_charges += ex.get("charges", 0.0)
                    
            open_trade["realized_pnl"] = total_pnl - total_charges
            
            if total_exited_qty >= entry_qty:
                open_trade["status"] = "CLOSED"
                
            self._write_data(data)
            return True
