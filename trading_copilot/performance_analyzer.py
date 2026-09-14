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
    def _primary_minute() -> int:
        """The horizon the system is optimised on -- the largest configured
        measurement checkpoint (improved §4.4)."""
        try:
            from core.policy_config import load_policy
            return max(int(m) for m in load_policy().horizon["measure_at_minutes"])
        except Exception:
            return 90

    @staticmethod
    def _compute_metrics(signals: list) -> dict:
        if not signals:
            return {}

        resolved = [s for s in signals if s.get("outcome", {}).get("status") in ("RESOLVED", "RESOLVED_EARLY")]
        if not resolved:
            return {"total_signals": len(signals), "total_resolved": 0}

        primary = PerformanceAnalyzer._primary_minute()
        p_dir = f"directional_correct_{primary}m"
        p_pnl = f"pnl_{primary}m_pct"

        # Primary-horizon win rate (what PerformanceAnalyzer optimises) plus a
        # 30m diagnostic that stays informational.
        win_primary = sum(1 for s in resolved if s.get("outcome", {}).get(p_dir, False))
        win_30m = sum(1 for s in resolved if s.get("outcome", {}).get("directional_correct_30m", False))

        pnl_sum_win = sum(s["outcome"][p_pnl] for s in resolved if s["outcome"].get(p_pnl, 0) > 0)
        pnl_sum_loss = sum(s["outcome"][p_pnl] for s in resolved if s["outcome"].get(p_pnl, 0) < 0)

        # No losing trade means the profit factor is UNDEFINED, not enormous.
        # This used to be 999.0 -- a sentinel with nothing behind it, rendered
        # to the operator as a measured "999" on a panel whose whole point is
        # that unmeasured things must not show as numbers. None propagates and
        # the UI renders it as n/a.
        profit_factor = (round(pnl_sum_win / abs(pnl_sum_loss), 2)
                         if pnl_sum_loss != 0 else None)

        hit_stop = sum(1 for s in resolved if s["outcome"].get("hit_stop", False))
        hit_target = sum(1 for s in resolved if s["outcome"].get("hit_target", False))

        total_resolved = len(resolved)
        primary_wr = round((win_primary / total_resolved) * 100, 2)

        return {
            "total_signals": len(signals),
            "total_resolved": total_resolved,
            "primary_horizon_min": primary,
            f"win_rate_{primary}m": primary_wr,
            "win_rate_primary": primary_wr,
            "win_rate_30m": round((win_30m / total_resolved) * 100, 2),
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
    def _cluster_accuracy_from(signals: list) -> dict:
        """Bucket by correlation cluster, reusing the shared map (Task 3.2)."""
        from core.risk import cluster_of, load_clusters
        clusters = load_clusters()
        return PerformanceAnalyzer._group_accuracy(
            signals,
            lambda s: cluster_of(
                str(s.get("symbol", "UNKNOWN")).split("-")[0].strip().upper(), clusters))

    @staticmethod
    def _best_worst(grouped: dict) -> tuple:
        """(best, worst) bucket by primary-horizon win rate, or (None, None).

        `_group_accuracy` has already dropped every bucket with zero resolved
        signals, so anything left here is genuinely measured. `n` travels with
        the answer so a one-signal "best regime" can be read for what it is.
        """
        scored = [{"key": k, "win_rate": m.get("win_rate_primary"),
                   "n": m.get("total_resolved", 0)}
                  for k, m in grouped.items() if m.get("win_rate_primary") is not None]
        if not scored:
            return None, None
        return (max(scored, key=lambda e: e["win_rate"]),
                min(scored, key=lambda e: e["win_rate"]))

    @staticmethod
    def session_review(signals: list, session_date: str) -> dict:
        """End-of-day review for ONE session date (Task 5.6).

        `measurement_state` is the honest bit. A session can be:
          NO_SIGNALS     -- nothing was emitted,
          NONE_RESOLVED  -- signals were emitted but none has an outcome at
                            the primary horizon yet,
          MEASURED       -- at least one resolved outcome exists.
        In the first two states `outcomes` is None and the grouped views are
        empty: there is no win rate to report, and 0.0 would be a lie.

        `legacy_excluded` counts signals resolved under a retired horizon --
        real evidence, just not evidence about the horizon in force. A signal
        stopped out or target-hit EARLY is measured, not excluded: the trade
        is over, and that is its outcome (core.outcome_schema).
        """
        from core.outcome_schema import (LEGACY, classify, measure_at_minutes,
                                          normalise)

        today = [s for s in signals if s.get("session_date") == session_date]
        primary = PerformanceAnalyzer._primary_minute()
        mins = measure_at_minutes()

        # A signal that cannot be attributed to the horizon config in force --
        # graded at a retired checkpoint, or written by the pre-C-3 resolver --
        # has no outcome at the primary horizon. Folding it in would score it a
        # loss and report a real-looking 0% win rate: the exact defect class
        # this project exists to remove. It is excluded and counted.
        #
        # An EARLY stop or target hit is the opposite case: the position is
        # closed, so that hit IS the primary-horizon outcome even though no
        # 90m key exists. normalise() projects it onto the primary keys so the
        # shared _compute_metrics reads it. Excluding those instead would leave
        # a win rate computed only from trades that hit neither stop nor
        # target -- a survivorship-filtered sample (see core.outcome_schema).
        legacy, measurable = [], []
        for s in today:
            if classify(s, mins) == LEGACY:
                legacy.append(s)
            else:
                measurable.append(normalise(s, mins) or s)   # None => still pending

        base = {
            "session_date": session_date,
            "primary_horizon_min": primary,
            "total_signals": len(today),
            "total_resolved": 0,
            "legacy_excluded": len(legacy),
            "measurement_state": "NO_SIGNALS" if not today else "NONE_RESOLVED",
            "outcomes": None,
            "by_regime": {},
            "by_cluster": {},
            "best_regime": None,
            "worst_regime": None,
            "best_cluster": None,
            "worst_cluster": None,
        }

        metrics = PerformanceAnalyzer._compute_metrics(measurable)
        if metrics.get("total_resolved", 0) == 0:
            return base

        by_regime = PerformanceAnalyzer._regime_accuracy_from(measurable)
        by_cluster = PerformanceAnalyzer._cluster_accuracy_from(measurable)
        best_r, worst_r = PerformanceAnalyzer._best_worst(by_regime)
        best_c, worst_c = PerformanceAnalyzer._best_worst(by_cluster)

        base.update({
            "total_resolved": metrics["total_resolved"],
            "measurement_state": "MEASURED",
            "outcomes": metrics,
            "by_regime": by_regime,
            "by_cluster": by_cluster,
            "best_regime": best_r,
            "worst_regime": worst_r,
            "best_cluster": best_c,
            "worst_cluster": worst_c,
        })
        return base

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
            from core.outcome_schema import (LEGACY, classify, measure_at_minutes,
                                             normalise)

            raw = SignalLedger.load_all_signals(last_n_days)       # ONE load
            mins = measure_at_minutes()

            # This payload is not a report -- conviction_scorer feeds it back
            # into the LIVE component weights. A record that cannot be
            # attributed to the horizon config in force carries no
            # `directional_correct_{primary}m` key, and _compute_metrics reads
            # a missing key as False: every such record was being scored a
            # LOSS, deflating regime win rates that then scale the dominant
            # component weight by up to +/-30% on every scored symbol. The same
            # records also padded the sample count past the `< 30` gate in
            # _get_adaptive_weights, so they helped open the gate they then
            # poisoned. Excluded and counted, exactly as the session review and
            # the reliability curve do (core.outcome_schema).
            legacy, measurable = [], []
            for s in raw:
                if classify(s, mins) == LEGACY:
                    legacy.append(s)
                else:
                    measurable.append(normalise(s, mins) or s)   # None => still pending

            signals = measurable
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
                    wr = m.get("win_rate_primary", 0.0)
                    if wr > best_wr:
                        best_wr = wr
                        best_regime = r
                    if wr < worst_wr:
                        worst_wr = wr
                        worst_regime = r

            payload = {
                # The sample gate in ConvictionScorer._get_adaptive_weights
                # reads this key. It counts attributable resolved records
                # only -- legacy_excluded is reported alongside, never folded
                # in.
                "total_signals": metrics["total_resolved"],
                "legacy_excluded": len(legacy),
                "primary_horizon_min": metrics.get("primary_horizon_min"),
                "win_rate_primary": metrics.get("win_rate_primary"),
                "win_rate_30m": metrics["win_rate_30m"],
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
