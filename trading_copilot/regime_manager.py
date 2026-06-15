import collections
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class SessionMemory:
    def __init__(self):
        self.buffer = collections.deque(maxlen=12)

    def _extract_regime_slice(self, payload: dict) -> dict:
        micro = payload.get("1_live_microstructure", {})
        deriv = payload.get("2_derivatives_matrix_52w", {})
        struct = payload.get("3_local_structural_edge_20d", {})
        prox = struct.get("structural_proximity_state", {})

        return {
            "timestamp": payload.get("timestamp"),
            "current_time": payload.get("current_time", ""),
            "ltp": payload.get("ltp"),
            "flow_divergence_state": micro.get("flow_divergence_state", "EQUILIBRIUM_CHOP"),
            "volume_regime": micro.get("volume_regime", "NORMAL_DRIFT"),
            "fractal_alignment": micro.get("fractal_alignment", "CONFLICTING_CHOP"),
            "elasticity_risk": micro.get("elasticity_risk", "EQUILIBRIUM"),
            "kinetic_divergence": micro.get("kinetic_divergence", "MOMENTUM_CONFIRMED"),
            "volatility_state": micro.get("volatility_state", "NORMAL_RANGING"),
            "volatility_regime_state": deriv.get("volatility_regime_state", "NORMAL_PRICING"),
            "options_gravity_state": deriv.get("options_gravity_state", "NORMAL_ORBIT"),
            "structural_proximity_state": prox.get("state", "NO_MANS_LAND")
        }

    def update(self, payload: dict):
        slice_data = self._extract_regime_slice(payload)
        self.buffer.append(slice_data)


class RegimeManager:
    def __init__(self):
        self.memory = SessionMemory()
        self.current_regime = "TRANSITIONAL_DRIFT"
        self.epochs_in_regime = 0

    @staticmethod
    def _get_session_phase(time_str: str) -> str:
        if not time_str:
            return "UNKNOWN"
        try:
            # Parse "10:30 am" format
            t = datetime.strptime(time_str, "%I:%M %p")
            hour = t.hour
            minute = t.minute
            
            if hour < 9 or (hour == 9 and minute < 45):
                return "OPENING_RANGE"
            elif hour < 11 or (hour == 11 and minute < 30):
                return "MORNING_SESSION"
            elif hour < 13 or (hour == 13 and minute < 30):
                return "LUNCH_CHOP"
            elif hour < 15:
                return "POWER_HOUR"
            else:
                return "CLOSE_AUCTION"
        except Exception:
            return "UNKNOWN"

    def _evaluate_candidate(self, snapshot: dict) -> str:
        flow_divergence = snapshot.get("flow_divergence_state", "EQUILIBRIUM_CHOP")
        volume_regime = snapshot.get("volume_regime", "NORMAL_DRIFT")
        fractal = snapshot.get("fractal_alignment", "CONFLICTING_CHOP")
        elasticity_risk = snapshot.get("elasticity_risk", "EQUILIBRIUM")
        volatility_state = snapshot.get("volatility_state", "NORMAL_RANGING")
        options_gravity_state = snapshot.get("options_gravity_state", "NORMAL_ORBIT")
        structural_proximity_state = snapshot.get("structural_proximity_state", "NO_MANS_LAND")

        # PRIORITY 1: SQUEEZE
        if "SQUEEZE" in volatility_state and volume_regime in ("ELEVATED_ACCUMULATION", "TIME_ADJUSTED_SHOCK"):
            return "PRE_BREAKOUT_SQUEEZE"

        # PRIORITY 2: MEAN REVERSION
        if "OVERSTRETCHED" in elasticity_risk and options_gravity_state != "ESCAPE_VELOCITY_ACHIEVED" and volume_regime != "TIME_ADJUSTED_SHOCK":
            return "MEAN_REVERSION_IMMINENT"

        # PRIORITY 3: TREND EXPANSION
        if "MOMENTUM_CONFIRMED" in flow_divergence and fractal in ("STRONG_FRACTAL_BULL", "STRONG_FRACTAL_BEAR") and volume_regime in ("TIME_ADJUSTED_SHOCK", "ELEVATED_ACCUMULATION") and "OVERSTRETCHED" not in elasticity_risk:
            return "TREND_EXPANSION"

        # PRIORITY 4: RANGE BOUND CHOP
        if flow_divergence == "EQUILIBRIUM_CHOP" and fractal in ("CONFLICTING_CHOP", "WEAK_FRACTAL_BULL", "WEAK_FRACTAL_BEAR") and volume_regime in ("NORMAL_DRIFT", "SUPPRESSED_FLOW") and structural_proximity_state != "TEST_IMMINENT":
            return "RANGE_BOUND_CHOP"

        # FALLBACK
        return "TRANSITIONAL_DRIFT"

    def determine_regime(self, payload: dict) -> dict:
        market_state = payload.get("market_state", "LIVE")
        time_str = payload.get("current_time", "")
        session_phase = self._get_session_phase(time_str)

        # 1. Closed Guard
        if market_state == "CLOSED":
            return {
                "current_regime": "MARKET_CLOSED",
                "session_phase": "CLOSE_AUCTION",
                "regime_age_epochs": 0,
                "just_transitioned": False
            }

        # 2. Extract & Store
        self.memory.update(payload)
        
        # 3. Evaluate Candidate
        snapshot = self.memory.buffer[-1]
        candidate = self._evaluate_candidate(snapshot)

        # 4. Hysteresis
        history = list(self.memory.buffer)
        if len(history) >= 3:
            recent_candidates = [self._evaluate_candidate(s) for s in history[-3:]]
            agreement = sum(1 for r in recent_candidates if r == candidate)
            if candidate != self.current_regime and agreement < 2:
                candidate = self.current_regime  # Reject change
                
        # 5. Transition Tracking
        just_transitioned = False
        if candidate != self.current_regime:
            just_transitioned = True
            self.current_regime = candidate
            self.epochs_in_regime = 1
        else:
            self.epochs_in_regime += 1

        return {
            "current_regime": self.current_regime,
            "session_phase": session_phase,
            "regime_age_epochs": self.epochs_in_regime,
            "just_transitioned": just_transitioned
        }


class RegimeManagerRegistry:
    _managers: dict[str, RegimeManager] = {}

    @classmethod
    def get_or_create(cls, symbol: str) -> RegimeManager:
        if symbol not in cls._managers:
            cls._managers[symbol] = RegimeManager()
        return cls._managers[symbol]
