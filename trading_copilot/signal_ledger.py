import os
import json
import time
import asyncio
import logging
import pandas as pd
from datetime import datetime, timedelta

from paths import SIGNALS_DIR
from journal.outcome_labeller import label_outcome

logger = logging.getLogger(__name__)

class SignalLedger:
    _pending_signals = {}  # signal_id -> dict

    # Conservative NSE intraday-equity round-trip cost estimate. Task 3.1
    # (Phase 3) replaces this with the real brokerage/STT/GST cost model;
    # until then this is what stands between "positive" and "directionally
    # correct" so a signal can't be scored a win purely on noise.
    ROUND_TRIP_COST_PCT = 0.06

    @classmethod
    def _get_log_file(cls, date_str=None):
        if not date_str:
            date_str = datetime.now().strftime("%Y-%m-%d")
        SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
        return str(SIGNALS_DIR / f"signal_log_{date_str}.jsonl")

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

        # build_structured_payload() deliberately sets these to None (not
        # absent) while a position is held, to hide new-entry geometry during
        # an active trade. `.get(key, default)` only applies the default when
        # the key is missing, so `math_setup.get("execution_geometry", {})`
        # returns the stored None and the chained .get() raised AttributeError
        # -- meaning CLOSE_EXISTING / REVERSE_POSITION signals were silently
        # never recorded.
        geo = math_setup.get("execution_geometry") or {}
        exp = math_setup.get("expectancy_matrix") or {}

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
                "implied_probability": exp.get("implied_probability"),
                "verdict": execution_ticket.get("verdict", "UNKNOWN"),
                "action_directive": execution_ticket.get("action_directive", "UNKNOWN"),
                "bias": bias,
                "padded_stop": geo.get("padded_stop", 0.0),
                "calculated_target": geo.get("calculated_target", 0.0),
                "calculated_entry": geo.get("calculated_entry", 0.0)
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
    def recover_pending(cls, days: int = 2) -> int:
        """Rebuild the in-memory pending set after a restart (fixes C-3d).

        _pending_signals was memory-only, so every signal younger than the
        resolution horizon at shutdown stayed PENDING forever and was
        silently excluded from all statistics — a restart quietly discarded
        the most recent, most relevant evidence.
        """
        restored = 0
        for rec in cls.load_all_signals(last_n_days=days):
            if rec.get("outcome", {}).get("status") != "PENDING":
                continue
            ts = rec["timestamp"]
            cls._pending_signals[rec["signal_id"]] = {
                "record": rec, "target_30m": ts + 1800, "target_60m": ts + 3600,
                "resolved_30m": False, "resolved_60m": False,
            }
            restored += 1
        logger.info(f"Recovered {restored} pending signals from disk.")
        return restored

    @classmethod
    async def _fetch_recent_bars(cls, token: str, n: int = 300):
        """Fetch recent LTF bars for `token` from the upstox_feed process.

        RollingStateEngine.dfs (and its ltf_df) lives only in the
        upstox_feed.py process's memory; this resolver runs inside
        main.py's process. api_server.py already bridges the two the same
        way (aiohttp GET against 127.0.0.1:8001) for /state — this follows
        that established pattern for bar data. Returns None on any
        failure (network, timeout, empty) so the caller skips gracefully
        rather than resolving against no data.
        """
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "http://127.0.0.1:8001/api/bars",
                    params={"token": token, "n": n},
                    timeout=aiohttp.ClientTimeout(total=3),
                ) as resp:
                    data = await resp.json()
        except Exception as e:
            logger.warning(f"Could not fetch bars for {token}: {e}")
            return None
        bars = data.get("bars") or []
        if not bars:
            return None
        df = pd.DataFrame(bars)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df

    @classmethod
    def _resolve_one(cls, record: dict, state: dict, now_ts: int, bars) -> bool:
        """Bar-accurate resolution for one pending signal (fixes C-3b/c).

        Pure given already-fetched bars — no network, no event loop — so it
        is unit-testable directly. Replaces the previous approach of
        sampling current_ltp once every 60s against stop/target (which
        missed any intrabar touch-and-recover) and grading a win as merely
        `pnl > 0` (which let a +0.001% move at the 60-minute mark count as
        correct). Returns whether `record`/`state` were mutated.
        """
        entry_ts = record["timestamp"]
        snap = record["signal_snapshot"]
        entry, bias = snap["ltp_at_signal"], snap["bias"]
        stop, target = snap["padded_stop"], snap["calculated_target"]

        need_30 = not state["resolved_30m"] and now_ts >= state["target_30m"]
        need_60 = not state["resolved_60m"] and now_ts >= state["target_60m"]
        if not (need_30 or need_60) or bars is None:
            return False

        updated = False
        if need_30:
            res = label_outcome(bars, entry_ts, entry, stop, target, bias,
                                horizon_min=30, cost_pct=cls.ROUND_TRIP_COST_PCT)
            record["outcome"]["pnl_30m_pct"] = res["pnl_pct"]
            record["outcome"]["directional_correct_30m"] = res["directional_correct"]
            record["outcome"]["hit_stop"] = record["outcome"].get("hit_stop") or res["hit_stop"]
            record["outcome"]["hit_target"] = record["outcome"].get("hit_target") or res["hit_target"]
            state["resolved_30m"] = True
            updated = True
            if res["outcome"] in ("STOP", "TARGET"):
                # Decided early — don't wait for the 60m mark to grade it.
                record["outcome"]["status"] = "RESOLVED_EARLY"
                state["resolved_60m"] = True

        if need_60 and not state["resolved_60m"]:
            res = label_outcome(bars, entry_ts, entry, stop, target, bias,
                                horizon_min=60, cost_pct=cls.ROUND_TRIP_COST_PCT)
            record["outcome"]["pnl_60m_pct"] = res["pnl_pct"]
            record["outcome"]["directional_correct_60m"] = res["directional_correct"]
            record["outcome"]["hit_stop"] = record["outcome"].get("hit_stop") or res["hit_stop"]
            record["outcome"]["hit_target"] = record["outcome"].get("hit_target") or res["hit_target"]
            record["outcome"]["status"] = "RESOLVED"
            state["resolved_60m"] = True
            updated = True

        return updated

    @classmethod
    async def start_outcome_resolver(cls):
        from diagnostic_ui import TerminalDashboard
        logger.info("Starting Signal Ledger Outcome Resolver Loop (60s)")
        cls.recover_pending()

        while True:
            try:
                now_ts = int(time.time())
                resolved_ids = []

                for sig_id, state in cls._pending_signals.items():
                    record = state["record"]
                    symbol = record["symbol"]

                    due = ((not state["resolved_30m"] and now_ts >= state["target_30m"]) or
                           (not state["resolved_60m"] and now_ts >= state["target_60m"]))
                    if not due:
                        if state["resolved_30m"] and state["resolved_60m"]:
                            resolved_ids.append(sig_id)
                        continue

                    # Find the live token key (active_states is keyed by the
                    # full instrument token, e.g. "NSE_EQ|SAIL"; `symbol` is
                    # the bare name, e.g. "SAIL").
                    token_key = next((k for k in TerminalDashboard.active_states
                                      if symbol in k), symbol)

                    bars = await cls._fetch_recent_bars(token_key)
                    updated = cls._resolve_one(record, state, now_ts, bars)

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
        log_dir = str(SIGNALS_DIR)
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
