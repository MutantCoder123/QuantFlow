import os
import json
import time
import asyncio
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class SignalLedger:
    _pending_signals = {}  # signal_id -> dict

    @classmethod
    def _get_log_file(cls, date_str=None):
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")
        log_dir = os.path.join(os.path.dirname(__file__), "data", "signals")
        os.makedirs(log_dir, exist_ok=True)
        return os.path.join(log_dir, f"signal_log_{date_str}.jsonl")

    @classmethod
    def _append_to_log(cls, record, date_str=None):
        try:
            with open(cls._get_log_file(date_str), "a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            logger.error(f"Failed to write to signal ledger: {e}")

    @classmethod
    def record_signal(cls, symbol, execution_ticket, math_setup, market_regime, ltp):
        ts = int(time.time())
        signal_id = f"SIG-{symbol}-{ts}"
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")

        bias = "LONG" if "LONG" in execution_ticket.get("action_directive", "").upper() else "SHORT"

        record = {
            "signal_id": signal_id,
            "symbol": symbol,
            "timestamp": ts,
            "datetime_ist": now.isoformat(),
            "session_date": date_str,
            "signal_snapshot": {
                "ltp_at_signal": ltp,
                "regime": market_regime.get("current_regime", "UNKNOWN"),
                "session_phase": market_regime.get("session_phase", "UNKNOWN"),
                "composite_score": math_setup.get("composite_score", 0.0),
                "implied_probability": math_setup.get("expectancy_matrix", {}).get("implied_probability", 0.0),
                "verdict": execution_ticket.get("verdict", "UNKNOWN"),
                "action_directive": execution_ticket.get("action_directive", "UNKNOWN"),
                "bias": bias,
                "padded_stop": math_setup.get("execution_geometry", {}).get("padded_stop", 0.0),
                "calculated_target": math_setup.get("execution_geometry", {}).get("calculated_target", 0.0),
                "calculated_entry": math_setup.get("execution_geometry", {}).get("calculated_entry", 0.0)
            },
            "outcome": {
                "status": "PENDING",
                "hit_stop": False,
                "hit_target": False
            }
        }

        # Track in memory for resolution
        cls._pending_signals[signal_id] = {
            "record": record,
            "target_30m": ts + 1800,
            "target_60m": ts + 3600,
            "resolved_30m": False,
            "resolved_60m": False
        }

        cls._append_to_log(record, date_str)

    @classmethod
    async def start_outcome_resolver(cls):
        from diagnostic_ui import TerminalDashboard
        logger.info("Starting Signal Ledger Outcome Resolver Loop (60s)")
        
        while True:
            try:
                now_ts = int(time.time())
                resolved_ids = []

                for sig_id, state in cls._pending_signals.items():
                    record = state["record"]
                    symbol = record["symbol"]
                    entry_ltp = record["signal_snapshot"]["ltp_at_signal"]
                    bias = record["signal_snapshot"]["bias"]

                    # Find live state
                    active_payload = None
                    for key, val in TerminalDashboard.active_states.items():
                        if symbol in key:
                            active_payload = val
                            break
                    
                    if not active_payload:
                        continue # Symbol not active right now

                    current_ltp = active_payload.get("ltp", 0.0)
                    if current_ltp == 0.0:
                        continue

                    updated = False

                    # Geometric check (Stop/Target hit)
                    stop = record["signal_snapshot"]["padded_stop"]
                    target = record["signal_snapshot"]["calculated_target"]
                    
                    if stop > 0 and not record["outcome"].get("hit_stop"):
                        if bias == "LONG" and current_ltp <= stop: 
                            record["outcome"]["hit_stop"] = True
                            updated = True
                        elif bias == "SHORT" and current_ltp >= stop: 
                            record["outcome"]["hit_stop"] = True
                            updated = True
                            
                    if target > 0 and not record["outcome"].get("hit_target"):
                        if bias == "LONG" and current_ltp >= target: 
                            record["outcome"]["hit_target"] = True
                            updated = True
                        elif bias == "SHORT" and current_ltp <= target: 
                            record["outcome"]["hit_target"] = True
                            updated = True

                    # Resolve 30m
                    if not state["resolved_30m"] and now_ts >= state["target_30m"]:
                        record["outcome"]["ltp_at_30m"] = current_ltp
                        record["outcome"]["pnl_30m_pct"] = ((current_ltp - entry_ltp) / entry_ltp * 100) if bias == "LONG" else ((entry_ltp - current_ltp) / entry_ltp * 100)
                        record["outcome"]["directional_correct_30m"] = record["outcome"]["pnl_30m_pct"] > 0
                        state["resolved_30m"] = True
                        updated = True

                    # Resolve 60m
                    if not state["resolved_60m"] and now_ts >= state["target_60m"]:
                        record["outcome"]["ltp_at_60m"] = current_ltp
                        record["outcome"]["pnl_60m_pct"] = ((current_ltp - entry_ltp) / entry_ltp * 100) if bias == "LONG" else ((entry_ltp - current_ltp) / entry_ltp * 100)
                        record["outcome"]["directional_correct_60m"] = record["outcome"]["pnl_60m_pct"] > 0
                        record["outcome"]["status"] = "RESOLVED"
                        state["resolved_60m"] = True
                        updated = True

                    # If hit stop/target early, we should resolve immediately to not skew 60m if it bounces
                    if (record["outcome"].get("hit_stop") or record["outcome"].get("hit_target")) and record["outcome"]["status"] != "RESOLVED":
                        record["outcome"]["status"] = "RESOLVED_EARLY"
                        state["resolved_60m"] = True # Prevent further resolution
                        updated = True

                    if updated:
                        cls._append_to_log(record, record["session_date"])

                    if state["resolved_30m"] and state["resolved_60m"]:
                        resolved_ids.append(sig_id)

                for sig_id in resolved_ids:
                    del cls._pending_signals[sig_id]

            except Exception as e:
                logger.error(f"Outcome Resolver Error: {e}")

            await asyncio.sleep(60)

    @classmethod
    def load_all_signals(cls, last_n_days=30) -> list:
        log_dir = os.path.join(os.path.dirname(__file__), "data", "signals")
        if not os.path.exists(log_dir):
            return []
            
        now = datetime.now()
        dates_to_check = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(last_n_days)]
        
        signals_map = {} # signal_id -> record (keeps the latest appended version)
        
        for date_str in dates_to_check:
            filepath = os.path.join(log_dir, f"signal_log_{date_str}.jsonl")
            if os.path.exists(filepath):
                with open(filepath, "r") as f:
                    for line in f:
                        try:
                            rec = json.loads(line.strip())
                            signals_map[rec["signal_id"]] = rec
                        except: pass
                        
        return list(signals_map.values())
