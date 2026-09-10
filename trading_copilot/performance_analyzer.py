import os
import json
import time
import logging
from signal_ledger import SignalLedger

logger = logging.getLogger(__name__)

class PerformanceAnalyzer:
    # TTL cache for get_feedback_payload (fixes D-1). It was re-reading the
    # entire signal ledger from disk ~100x/sec because conviction_scorer calls
    # it on every scored symbol on every cycle, and each call itself did two
    # full load_all_signals() scans.
    _cache = None            # dict | None
    _cache_ts = 0.0
    _TTL = 300.0

    @classmethod
    def invalidate_cache(cls) -> None:
        cls._cache, cls._cache_ts = None, 0.0

    @staticmethod
    def _compute_metrics(signals: list) -> dict:
        if not signals:
            return {}

        resolved = [s for s in signals if s.get("outcome", {}).get("status") in ("RESOLVED", "RESOLVED_EARLY")]
        if not resolved:
            return {"total_signals": len(signals), "total_resolved": 0}

        win_30m = sum(1 for s in resolved if s.get("outcome", {}).get("directional_correct_30m", False))
        win_60m = sum(1 for s in resolved if s.get("outcome", {}).get("directional_correct_60m", False))

        pnl_sum_win = sum(s["outcome"]["pnl_30m_pct"] for s in resolved if s["outcome"].get("pnl_30m_pct", 0) > 0)
        pnl_sum_loss = sum(s["outcome"]["pnl_30m_pct"] for s in resolved if s["outcome"].get("pnl_30m_pct", 0) < 0)

        profit_factor = round(pnl_sum_win / abs(pnl_sum_loss), 2) if pnl_sum_loss != 0 else 999.0

        hit_stop = sum(1 for s in resolved if s["outcome"].get("hit_stop", False))
        hit_target = sum(1 for s in resolved if s["outcome"].get("hit_target", False))

        total_resolved = len(resolved)

        return {
            "total_signals": len(signals),
            "total_resolved": total_resolved,
            "win_rate_30m": round((win_30m / total_resolved) * 100, 2),
            "win_rate_60m": round((win_60m / total_resolved) * 100, 2),
            "profit_factor": profit_factor,
            "stop_hit_rate": round((hit_stop / total_resolved) * 100, 2),
            "target_hit_rate": round((hit_target / total_resolved) * 100, 2)
        }

    @staticmethod
    def _group_accuracy(signals: list, key_fn) -> dict:
        buckets = {}
        for s in signals:
            k = key_fn(s)
            buckets.setdefault(k, []).append(s)
        res = {}
        for k, sigs in buckets.items():
            metrics = PerformanceAnalyzer._compute_metrics(sigs)
            if metrics.get("total_resolved", 0) > 0:
                res[k] = metrics
        return res

    @staticmethod
    def _regime_accuracy_from(signals: list) -> dict:
        return PerformanceAnalyzer._group_accuracy(
            signals, lambda s: s.get("signal_snapshot", {}).get("regime", "UNKNOWN"))

    @staticmethod
    def _symbol_accuracy_from(signals: list) -> dict:
        return PerformanceAnalyzer._group_accuracy(
            signals, lambda s: s.get("symbol", "UNKNOWN"))

    @staticmethod
    def compute_regime_accuracy(last_n_days: int = 30) -> dict:
        return PerformanceAnalyzer._regime_accuracy_from(SignalLedger.load_all_signals(last_n_days))

    @staticmethod
    def compute_symbol_accuracy(last_n_days: int = 30) -> dict:
        return PerformanceAnalyzer._symbol_accuracy_from(SignalLedger.load_all_signals(last_n_days))

    @staticmethod
    def compute_dashboard(last_n_days: int = 30) -> dict:
        signals = SignalLedger.load_all_signals(last_n_days)   # ONE load for all three
        return {
            "overall": PerformanceAnalyzer._compute_metrics(signals),
            "by_regime": PerformanceAnalyzer._regime_accuracy_from(signals),
            "by_symbol": PerformanceAnalyzer._symbol_accuracy_from(signals),
        }

    @staticmethod
    def get_feedback_payload(last_n_days: int = 14) -> dict:
        cls = PerformanceAnalyzer
        now = time.monotonic()
        if cls._cache is not None and (now - cls._cache_ts) < cls._TTL:
            return cls._cache
        try:
            signals = SignalLedger.load_all_signals(last_n_days)   # ONE load
            metrics = cls._compute_metrics(signals)

            if metrics.get("total_resolved", 0) == 0:
                cls._cache, cls._cache_ts = {}, now
                return {}

            regime_acc = cls._regime_accuracy_from(signals)        # reuse, don't reload

            best_regime = "UNKNOWN"
            best_wr = 0.0
            worst_regime = "UNKNOWN"
            worst_wr = 100.0

            for r, m in regime_acc.items():
                if m["total_resolved"] >= 3:
                    if m["win_rate_30m"] > best_wr:
                        best_wr = m["win_rate_30m"]
                        best_regime = r
                    if m["win_rate_30m"] < worst_wr:
                        worst_wr = m["win_rate_30m"]
                        worst_regime = r

            payload = {
                "total_signals": metrics["total_resolved"],
                "win_rate_30m": metrics["win_rate_30m"],
                "win_rate_60m": metrics["win_rate_60m"],
                "profit_factor": metrics["profit_factor"],
                "best_regime": best_regime,
                "best_regime_wr": best_wr,
                "worst_regime": worst_regime,
                "worst_regime_wr": worst_wr,
                "regime_accuracy": regime_acc
            }
            cls._cache, cls._cache_ts = payload, now
            return payload
        except Exception as e:
            logger.error(f"Failed to compute feedback payload: {e}")
            return {}
