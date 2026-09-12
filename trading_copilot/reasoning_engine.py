import os
import asyncio
import json
import logging
import re
import time
import datetime
import collections
from google import genai
from diagnostic_ui import TerminalDashboard

logger = logging.getLogger(__name__)


def clamp_risk_parameters(risk_params: dict, geo: dict, atr15: float, bias: str):
    """Enforce the adjustment bounds the prompt states but cannot guarantee (A-10).

    The prompt tells the LLM it may only nudge risk_parameters within small
    bounds around the deterministic math geometry, but nothing previously
    enforced that — a hallucinated stop or target reached the UI as an
    actionable price. This clamps every field to the stated bounds and
    rejects an internally inconsistent geometry outright.

    Returns (clamped_params, error). On error (or when risk_params is empty/
    missing), the deterministic geometry is returned unchanged so the
    operator never sees a hallucinated price.
    """
    fallback = {"final_entry": geo["calculated_entry"],
                "final_stop": geo["padded_stop"],
                "final_target": geo["calculated_target"]}
    if not risk_params:
        return fallback, None

    def _f(key, default):
        try:
            v = float(risk_params.get(key))
            return v if v > 0 else default
        except (TypeError, ValueError):
            return default

    entry_raw = _f("final_entry", geo["calculated_entry"])
    stop_raw = _f("final_stop", geo["padded_stop"])
    target_raw = _f("final_target", geo["calculated_target"])

    lo_e, hi_e = geo["calculated_entry"] * 0.997, geo["calculated_entry"] * 1.003
    entry = min(max(entry_raw, lo_e), hi_e)

    # Reject on the RAW stop/target against the (already-bounded) entry, not
    # on the post-clamp values: the stop's own ATR band sits entirely below
    # the entry's clamp band in every normal configuration, so checking
    # ordering after clamping the stop makes this branch unreachable — a
    # stop hallucinated on the wrong side of the position would silently
    # clamp into a plausible-looking (but never-intended) number instead of
    # being rejected. Checking the raw value here is what actually catches
    # "stop above entry for a long" while still tolerating an entry nudge
    # that legitimately needed reining in.
    if bias == "LONG":
        ordered = stop_raw < entry < target_raw
    else:
        ordered = target_raw < entry < stop_raw
    if not ordered:
        return fallback, "LLM_GEOMETRY_REJECTED"

    lo_s, hi_s = sorted((geo["padded_stop"] - 1.0 * atr15,
                         geo["padded_stop"] + 0.5 * atr15))
    stop = min(max(stop_raw, lo_s), hi_s)

    if bias == "LONG":
        target = max(target_raw, geo["calculated_target"] - 0.5 * atr15)
    else:
        target = min(target_raw, geo["calculated_target"] + 0.5 * atr15)

    return {"final_entry": round(entry, 2), "final_stop": round(stop, 2),
            "final_target": round(target, 2)}, None


def _classify_decision(math_setup: dict, gatekeeper_res: dict) -> str:
    """PROPOSED | REJECTED_<reason> | GATED_<reason> — feeds FeatureLog (1.4)."""
    if math_setup.get("setup_rejected", True):
        return f"REJECTED_{math_setup.get('rejection_reason', 'UNKNOWN')}"
    elif gatekeeper_res.get("llm_authorized"):
        return "PROPOSED"
    else:
        return f"GATED_{gatekeeper_res.get('math_rejection', 'UNKNOWN')}"


def _numeric_features(payload: dict) -> dict:
    """Flatten a payload to its numeric fields only, for FeatureLog (1.4).

    bool is excluded even though it's an int subclass — True/False is not a
    feature value.
    """
    return {k: float(v) for k, v in payload.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)}


class ReasoningEngine:
    # Throttle concurrent API calls to avoid rate limit bans (Increased for Tier 1)
    llm_semaphore = asyncio.Semaphore(15)
    
    # Store background tasks for toggling
    active_loops = {}
    
    # Store the latest generated reports
    latest_reports = {}

    # Store user positions dynamically
    user_positions = {}
    
    # Phase 9: Global Alerting Matrix
    global_alerts = []
    alert_counter = 0
    
    # Layer 2: Decision History (anti-whipsaw temporal context)
    decision_history = {}

    # State Machine Memory to prevent redundant LLM triggers
    last_math_advice = {}
    advice_debounce = {}
    llm_trigger_count = 0

    @staticmethod
    def _normalize_symbol(raw: str) -> str:
        """Extract the bare stock name from any key format.
        'NSE_EQ|SAIL-EQ' → 'SAIL', 'NSE_EQ|SAIL' → 'SAIL', 'SAIL' → 'SAIL'"""
        s = raw.split('|')[-1]    # strip exchange prefix
        s = s.split('-')[0]        # strip -EQ suffix
        return s

    @classmethod
    def _get_token_for_symbol(cls, symbol: str):
        from api_server import TerminalDashboard
        for token in TerminalDashboard.active_states.keys():
            # Match token like 'NSE_EQ|SAIL' or 'NSE_EQ|SAIL-EQ'
            stock_name = token.split('|')[-1]
            if stock_name == symbol or stock_name.split('-')[0] == symbol:
                return token
        return None

    @classmethod
    def build_structured_payload(cls, symbol: str, payload: dict, user_position: dict = None,
                                 user_intent: dict = None, *, advance_state: bool = False) -> dict:
        from mtf_extractor import sanitize_for_json
        from semantic_tagger import SemanticTagger
        import time
        
        if user_position is None:
            user_position = cls.user_positions.get(cls._normalize_symbol(symbol))
            
        # Inject Global Market Context & Catalyst into flat payload BEFORE translation
        from diagnostic_ui import TerminalDashboard
        if TerminalDashboard.global_market_context:
            payload["global_market_context"] = TerminalDashboard.global_market_context
            
        clean_sym = symbol.split('|')[-1]
        catalyst = TerminalDashboard.catalyst_cache.get(clean_sym)
        if catalyst and "raw_news" in catalyst:
            payload["raw_news"] = catalyst["raw_news"]
            
        tactical_payload = SemanticTagger.translate_to_llm_payload(payload)
        
        # Inject Regime
        from regime_manager import RegimeManagerRegistry
        manager = RegimeManagerRegistry.get_or_create(symbol)
        regime_metadata = (manager.determine_regime(tactical_payload) if advance_state
                           else manager.peek_regime())
        tactical_payload["market_regime"] = regime_metadata

        # Inject Conviction Score & Math Setup
        from conviction_scorer import ConvictionScorerRegistry
        scorer = ConvictionScorerRegistry.get_or_create(symbol)
        math_setup = scorer.score_setup(tactical_payload, payload, advance_state=advance_state)
        tactical_payload["math_setup"] = math_setup
            
        if user_position:
            # Dynamically calculate how long the position has been held
            entry_timestamp = user_position.get("entry_timestamp", time.time())
            time_in_trade_minutes = (time.time() - entry_timestamp) / 60.0
            if time_in_trade_minutes < 60:
                user_position["duration_held"] = f"{int(time_in_trade_minutes)} minutes"
            else:
                hours = int(time_in_trade_minutes // 60)
                mins = int(time_in_trade_minutes % 60)
                user_position["duration_held"] = f"{hours}h {mins}m"
                
            if "user_context" not in tactical_payload:
                tactical_payload["user_context"] = {}
            tactical_payload["user_context"]["position"] = user_position
            
            # Strip geometry to prevent the LLM and UI from showing new entry suggestions during an active trade
            tactical_payload["math_setup"]["execution_geometry"] = None
            tactical_payload["math_setup"]["expectancy_matrix"] = None
            
        if user_intent:
            if "user_context" not in tactical_payload:
                tactical_payload["user_context"] = {}
            tactical_payload["user_context"]["intent"] = user_intent

        # Inject Decision History for Layer 2 anti-whipsaw context
        tactical_payload["decision_history"] = list(
            cls.decision_history.get(symbol, [])
        )

        return sanitize_for_json(tactical_payload)

    @classmethod
    async def analyze_stock(cls, symbol: str, model_name: str = "gemini-2.5-flash", prompt_override: str = None, user_position: dict = None, user_intent: dict = None, is_autonomous: bool = False, precomputed_payload: dict = None) -> str:
        target_token = None
        if symbol in TerminalDashboard.active_states:
            target_token = symbol
        else:
            for k, v in TerminalDashboard.active_states.items():
                if symbol in k or v.get('symbol') == symbol:
                    target_token = k
                    break
                    
        if not target_token:
            msg = f"No active live data for {symbol}."
            cls.latest_reports[cls._normalize_symbol(symbol)] = msg
            return msg

        if precomputed_payload:
            payload_copy = precomputed_payload
        else:
            payload = TerminalDashboard.active_states[target_token]
            payload_copy = cls.build_structured_payload(symbol, payload, user_position, user_intent)

        if payload_copy.get("math_setup", {}).get("setup_rejected", True):
            # TOKEN SAVING FIREWALL: Do NOT call the LLM API.
            current_time = payload_copy.get('current_time', 'UNKNOWN')
            print(f"[{current_time}] SETUP REJECTED BY MATH ENGINE. LLM bypassed to save tokens.")
            math_setup = payload_copy.get("math_setup", {})
            ui_data = {
                "Action": math_setup.get("directional_bias", "Wait"),
                "Reason": "Math Engine Rejection: " + math_setup.get("rejection_reason", "LLM bypassed to save tokens."),
                "Entry_Target_Price": 0.0,
                "Stoploss": 0.0,
                "Exit_Target_Price": 0.0,
                "Confidence_Score": 0,
                "Priority_Score": 0,
                "Status_Tag": "",
                "math_rejection": math_setup.get("rejection_reason", "UNKNOWN"),
                "llm_authorized": False,
                "Generated_Time": current_time
            }
            rejection_msg = json.dumps(ui_data, indent=2)
            cls.latest_reports[cls._normalize_symbol(symbol)] = rejection_msg
            return rejection_msg

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            error_msg = "Error: GEMINI_API_KEY is missing from the environment."
            cls.latest_reports[cls._normalize_symbol(symbol)] = error_msg
            return error_msg

        # We lock this exact API call so at most 3 execute at the same time globally
        async with cls.llm_semaphore:
            try:
                cls.llm_trigger_count += 1
                logger.info(f"Triggering LLM Reasoning for {symbol} using {model_name}...")
                client = genai.Client(api_key=api_key)
                
                # Format payload for the prompt without indentation to save tokens
                user_payload = json.dumps(payload_copy, separators=(',', ':'))
                
                # Combine system prompt and user payload.
                default_prompt = (
                    "ROLE: Senior Institutional Portfolio Manager & Chief Risk Officer.\n"
                    "You are the Layer 2 Qualitative Judge in an autonomous intraday trading pipeline. "
                    "Layer 1 (the deterministic Math Engine) has already scored, normalized, mapped the volatility regime, "
                    "and generated a strict mathematical trade proposal in the `math_setup` block.\n\n"
                    "YOUR EXCLUSIVE FUNCTION: Resolve ambiguity and synthesize contradictions across "
                    "the semantic data blocks to determine if the Math Engine's proposal survives "
                    "real-world qualitative scrutiny. You do NOT invent trades or calculate geometry.\n\n"
                    "PRIME DIRECTIVES:\n\n"
                    "1. MATH IS BASELINE:\n"
                    "Treat `math_setup.execution_geometry` (calculated_entry, padded_stop, calculated_target) "
                    "as the deterministic baseline. Do not recalculate. Only modify if a catastrophic "
                    "qualitative event demands it, and only within the ADJUSTMENT CONSTRAINTS below.\n"
                    "`math_setup.expectancy_matrix` is NOT ground truth. `breakeven_probability` and "
                    "`reward_risk` are derived from the geometry and are reliable, but "
                    "`implied_probability` and `statistical_edge` are null whenever the system has not "
                    "yet measured its own hit rate (see `calibration_status`). When they are null, "
                    "reason from reward:risk and the qualitative blocks -- do NOT invent, assume, or "
                    "state a win probability.\n\n"
                    "2. HOLISTIC SYNTHESIS (SEEK CONTRADICTIONS):\n"
                    "Cross-reference the math against qualitative context across these blocks. "
                    "Your attention weighting MUST follow the active regime:\n\n"
                    "  BLOCK 1 — `1_live_microstructure`:\n"
                    "  Is the mathematical breakout supported by organic order flow (flow_divergence_state, "
                    "volume_regime), or is it a low-volume anomaly? In TREND_EXPANSION this block is paramount. "
                    "In RANGE_BOUND_CHOP it is misleading noise.\n\n"
                    "  BLOCK 2 — `2_derivatives_matrix_52w`:\n"
                    "  Are options markets (volatility_regime_state, pcr_regime) pricing in a volatility crush "
                    "or expansion that invalidates the mathematical target? In RANGE_BOUND_CHOP and "
                    "MEAN_REVERSION_IMMINENT this block is dominant.\n\n"
                    "  BLOCK 3 — `3_local_structural_edge_20d`:\n"
                    "  Are the math engine's stop and target anchored to genuine structural walls? "
                    "If structural_proximity_state.state is TEST_IMMINENT at a Value Area boundary, "
                    "the math's geometry may be about to get invalidated. Cross-check camarilla_pivots "
                    "against execution_geometry boundaries. In PRE_BREAKOUT_SQUEEZE this block is critical.\n\n"
                    "  BLOCK 4 — `4_catalyst_engine`:\n"
                    "  Does the macroeconomic narrative align with the mathematical momentum? "
                    "If math signals LONG but catalyst is catastrophic bearish, you MUST ABORT. "
                    "If raw_news is empty, treat catalyst as NEUTRAL — do not infer from absence.\n\n"
                    "  BLOCK 5 — `market_regime`:\n"
                    "  Is the math proposal coherent with the active regime? A TREND_EXPANSION setup in "
                    "RANGE_BOUND_CHOP demands extreme scrutiny. If just_transitioned is true, the math scores "
                    "may reflect the OLD regime — demand extra confluence before CONFIRMing. "
                    "session_phase provides time-of-day context (OPENING_RANGE, LUNCH_CHOP, POWER_HOUR).\n\n"
                    "  BLOCK 6 — `decision_history`:\n"
                    "  If this array contains previous verdicts, check for whipsaw. If you are reversing "
                    "a directional call from the last 2 entries, demand overwhelming multi-block evidence. "
                    "If the array is empty, this is a fresh session — proceed normally.\n\n"
                    "3. USER CONTEXT RESOLUTION:\n"
                    "Read `user_context`. If user holds a LONG position and math proposes SHORT, "
                    "your action_directive must reflect portfolio management: CLOSE_EXISTING or "
                    "REVERSE_POSITION — never open a conflicting position silently. "
                    "CRITICAL: If user holds a LONG position and math proposes LONG (or user holds SHORT and math proposes SHORT), "
                    "your action_directive MUST be HOLD. Do NOT output EXECUTE_LONG or EXECUTE_SHORT for an already active position. "
                    "If user holds an active position, `execution_geometry` will intentionally be null. Do NOT abort due to missing geometry. "
                    "If user_context is empty, treat user as IDLE with no positions.\n\n"
                    "4. ADJUSTMENT CONSTRAINTS:\n"
                    "If verdict is ADJUST, you may ONLY modify risk_parameters within these bounds:\n"
                    "  - final_stop: Widen by at most 1x ATR(15m) or tighten by at most 0.5x ATR(15m) "
                    "from math_setup's padded_stop.\n"
                    "  - final_target: Reduce by at most 0.5x ATR(15m).\n"
                    "  - final_entry: Must remain within ±0.3% of math_setup's calculated_entry.\n"
                    "  - Any adjustment MUST be justified in institutional_rationale by citing the "
                    "specific qualitative signal.\n\n"
                    "5. NULL SAFETY:\n"
                    "  - If global_market_context is null, skip macro synthesis.\n"
                    "  - If any block contains null/0 values, default to NEUTRAL for that block.\n"
                    "  - If market_state is CLOSED or AUCTION, immediately output verdict ABORT "
                    "with action_directive PASS.\n\n"
                    "6. RATIONALE:\n"
                    "institutional_rationale must be exactly 2-3 sentences of dense, institutional logic. "
                    "No definitions. State which blocks agree, which contradict, and why one dominates.\n\n"
                    "OUTPUT: Emit ONLY a raw JSON object passable to json.loads(). "
                    "No markdown, no backticks, no commentary.\n\n"
                    "{\"execution_ticket\":{"
                    "\"verdict\":\"CONFIRM|DEFER|ABORT|ADJUST\","
                    "\"action_directive\":\"EXECUTE_LONG|EXECUTE_SHORT|PASS|CLOSE_EXISTING|REVERSE_POSITION\","
                    "\"conviction_modifier\":0.0,"
                    "\"urgency\":\"IMMEDIATE|LIMIT_ONLY|WAIT_FOR_PULLBACK\","
                    "\"regime_echo\":\"<active regime string for audit>\","
                    "\"institutional_rationale\":\"<2-3 dense sentences>\","
                    "\"risk_parameters\":{"
                    "\"final_entry\":0.0,"
                    "\"final_stop\":0.0,"
                    "\"final_target\":0.0"
                    "}}}"
                )
                
                strict_prompt = default_prompt + "\n\nCRITICAL: You are a machine-readable API endpoint. Output ONLY the JSON object. No markdown blocks, no footnotes, no preamble. NEVER use double quotes inside string values (use single quotes instead)."
                
                if prompt_override:
                    strict_prompt = prompt_override + "\n\n" + strict_prompt
                    
                # Phase 10: Inject feedback calibration into prompt
                try:
                    from performance_analyzer import PerformanceAnalyzer
                    feedback = PerformanceAnalyzer.get_feedback_payload(last_n_days=14)
                    if feedback and feedback.get("total_signals", 0) >= 20:
                        feedback_block = (
                            f"\n\nHISTORICAL CALIBRATION (last 14 days, {feedback['total_signals']} signals):\n"
                            f"- Overall 30m directional accuracy: {feedback['win_rate_30m']}%\n"
                            f"- Overall 60m directional accuracy: {feedback['win_rate_60m']}%\n"
                            f"- Profit factor: {feedback['profit_factor']}\n"
                            f"- Best regime: {feedback['best_regime']} ({feedback['best_regime_wr']}% win rate)\n"
                            f"- Worst regime: {feedback['worst_regime']} ({feedback['worst_regime_wr']}% win rate)\n"
                            "Calibrate your conviction_modifier accordingly. Be MORE aggressive in regimes "
                            "where historical accuracy is high, and MORE cautious where it is low."
                        )
                        strict_prompt += feedback_block
                except Exception:
                    pass
                
                full_prompt = f"SYSTEM INSTRUCTION:\n{strict_prompt}\n\nDATA PAYLOAD:\n{user_payload}"
                
                response = await client.aio.models.generate_content(
                    model=model_name,
                    contents=full_prompt
                )
                
                report_text = response.text.strip()
                # Clean markdown blocks if LLM hallucinated them
                if report_text.startswith("```json"):
                    report_text = report_text[7:]
                if report_text.startswith("```"):
                    report_text = report_text[3:]
                if report_text.endswith("```"):
                    report_text = report_text[:-3]
                report_text = report_text.strip()
                
                # Phase 9: Parse and Trigger Alerts
                try:
                    data = json.loads(report_text)
                except json.JSONDecodeError as jde:
                    import re
                    # Fallback 1: Strip extra text (e.g. trailing commentary) by extracting outermost braces
                    json_match = re.search(r'(\{.*\})', report_text, re.DOTALL)
                    if json_match:
                        clean_json = json_match.group(1)
                        try:
                            data = json.loads(clean_json)
                        except json.JSONDecodeError as nested_jde:
                            # Fallback 2: fix unescaped double quotes inside institutional_rationale
                            rationale_match = re.search(r'"institutional_rationale"\s*:\s*"(.*?)"\s*,\s*"risk_parameters"', clean_json, re.DOTALL)
                            if rationale_match:
                                bad_rationale = rationale_match.group(1)
                                good_rationale = bad_rationale.replace('"', "'")
                                clean_json = clean_json[:rationale_match.start(1)] + good_rationale + clean_json[rationale_match.end(1):]
                                data = json.loads(clean_json)
                            else:
                                raise nested_jde
                    else:
                        raise jde
                except Exception as e:
                    raise e
                    
                try:
                    ticket = data.get("execution_ticket", {})
                    verdict = ticket.get("verdict", "UNKNOWN")
                    action = ticket.get("action_directive", "UNKNOWN")
                    
                    # Convert LLM execution ticket back to UI-compatible format
                    ui_action = "Wait"
                    if "LONG" in action: ui_action = "Long"
                    elif "SHORT" in action: ui_action = "Short"
                    elif "CLOSE" in action: ui_action = "Close"
                    elif "HOLD" in action: ui_action = "Hold"
                    
                    # Calculate priority & confidence mirroring Gatekeeper math
                    math_setup = payload_copy.get("math_setup") or {}
                    composite_score = math_setup.get("composite_score", 0.0)
                    
                    expectancy_matrix = math_setup.get("expectancy_matrix") or {}
                    # None while uncalibrated (§4.2) -- no measured probability,
                    # so no confidence number is reported rather than an invented one.
                    stat_edge = expectancy_matrix.get("statistical_edge")

                    calc_priority = min(10, int(abs(composite_score) * 20))
                    calc_confidence = (min(10, int(stat_edge * 33))
                                       if stat_edge is not None and stat_edge > 0 else 0)
                    
                    geo_src = (math_setup.get("execution_geometry") or {})
                    geo_err = None
                    if geo_src:
                        atr15 = float(payload_copy.get("atr_15m")
                                      or payload_copy.get("ltp", 100.0) * 0.005)
                        risk_params, geo_err = clamp_risk_parameters(
                            ticket.get("risk_parameters") or {}, geo_src, atr15,
                            math_setup.get("directional_bias", "LONG"))
                    else:
                        # No deterministic geometry to clamp against (e.g. a
                        # CLOSE/HOLD ticket) — never pass the raw LLM numbers
                        # through unvalidated.
                        risk_params = {"final_entry": 0.0, "final_stop": 0.0,
                                       "final_target": 0.0}
                    ui_data = {
                        "Action": ui_action,
                        "Reason": ticket.get("institutional_rationale", verdict),
                        "Entry_Target_Price": risk_params.get("final_entry", 0.0),
                        "Stoploss": risk_params.get("final_stop", 0.0),
                        "Exit_Target_Price": risk_params.get("final_target", 0.0),
                        "Confidence_Score": calc_confidence,
                        "Priority_Score": calc_priority,
                        "Status_Tag": "LLM_ANALYZED",
                        "llm_authorized": True,
                        "geometry_override": geo_err,
                        "Edge_Status": expectancy_matrix.get("calibration_status"),
                        "Reward_Risk": expectancy_matrix.get("reward_risk"),
                        "Generated_Time": payload_copy.get("current_time", "UNKNOWN")
                    }
                    cls.latest_reports[cls._normalize_symbol(symbol)] = json.dumps(ui_data, indent=2)
                    
                    # Record into decision_history deque
                    cls.decision_history.setdefault(
                        symbol, collections.deque(maxlen=5)
                    ).append({
                        "time": payload_copy.get("current_time", ""),
                        "verdict": verdict,
                        "action": action,
                        "composite_score": payload_copy.get("math_setup", {}).get("composite_score"),
                        "ltp": payload_copy.get("ltp")
                    })
                    
                    # ---- Shadow mode: journal BOTH arms for every escalation,
                    # unconditionally on verdict (improved §4.5). Previously only
                    # CONFIRM/ADJUST reached the ledger, so every ABORT/DEFER
                    # vanished and "does the LLM actually help?" was unanswerable.
                    cls._write_arm_record(symbol, math_setup, ticket, risk_params)

                    actionable_directives = ["EXECUTE_LONG", "EXECUTE_SHORT", "CLOSE_EXISTING", "REVERSE_POSITION"]
                    if verdict in ("CONFIRM", "ADJUST") and action in actionable_directives:
                        # Phase 10: Record to Signal Ledger for outcome tracking
                        if is_autonomous:
                            from signal_ledger import SignalLedger
                            SignalLedger.record_signal(
                                symbol=symbol,
                                execution_ticket=ticket,
                                math_setup=payload_copy.get("math_setup", {}),
                                market_regime=payload_copy.get("market_regime", {}),
                                ltp=payload_copy.get("ltp", 0.0)
                            )
                            
                        cls.alert_counter += 1
                        cls.global_alerts.insert(0, {
                            "id": cls.alert_counter,
                            "timestamp": time.time(),
                            "symbol": symbol,
                            "verdict": verdict,
                            "action": action,
                            "rationale": ticket.get("institutional_rationale", ""),
                            "read": False
                        })
                        # Cap at 50 alerts in history to prevent memory leak
                        if len(cls.global_alerts) > 50:
                            cls.global_alerts.pop()
                            
                        logger.warning(f"🚨 SYSTEM ALERT TRIGGERED for {symbol}: {verdict} → {action}")
                        
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse LLM JSON output for {symbol}: {e}")
                    logger.error(f"Raw output: {report_text}")
                
                logger.info(f"Successfully generated reasoning report for {symbol}.")
                return report_text
                
            except Exception as e:
                logger.error(f"Reasoning Engine API Exception for {symbol}: {e}")
                error_msg = f"Error generating report: {str(e)}"
                cls.latest_reports[cls._normalize_symbol(symbol)] = error_msg
                return error_msg

    llm_enabled = {} # symbol -> bool

    _arm_journal = None

    @classmethod
    def _get_arm_journal(cls):
        if cls._arm_journal is None:
            from journal.arms import ArmJournal
            from paths import SIGNALS_DIR
            cls._arm_journal = ArmJournal(SIGNALS_DIR / "arms")
        return cls._arm_journal

    @classmethod
    def _write_arm_record(cls, symbol, math_setup, ticket, risk_params):
        """One ArmRecord per escalation, whatever the LLM said (§4.5)."""
        try:
            from journal.arms import ArmRecord
            geo = (math_setup or {}).get("execution_geometry") or {}
            try:
                from core.policy_config import load_policy
                cfg_version = load_policy().version
            except Exception:
                cfg_version = 0
            cls._get_arm_journal().write(ArmRecord(
                symbol=cls._normalize_symbol(symbol),
                ts=int(time.time()),
                config_version=cfg_version,
                math_arm={
                    "action": (math_setup or {}).get("directional_bias", "NEUTRAL"),
                    "entry": geo.get("calculated_entry"),
                    "stop": geo.get("padded_stop"),
                    "target": geo.get("calculated_target"),
                    "composite": (math_setup or {}).get("composite_score"),
                },
                llm_arm={
                    "verdict": ticket.get("verdict", "UNKNOWN"),
                    "action": ticket.get("action_directive", "UNKNOWN"),
                    "entry": risk_params.get("final_entry"),
                    "stop": risk_params.get("final_stop"),
                    "target": risk_params.get("final_target"),
                },
                escalated=True,
            ))
        except Exception as e:
            logger.error(f"Failed to journal arm record for {symbol}: {e}")

    @classmethod
    def _build_portfolio(cls):
        """Reconstruct an L3 Portfolio from the tracked user positions.

        Each position contributes qty*|entry-stop| of risk to its cluster
        when it carries a numeric quantity AND a stoploss; positions
        missing either contribute nothing (they cannot be sized). There is
        no realised-P&L feed in this process, so realized_loss_today stays
        0.0 -- the daily-loss breaker is a documented no-op here until a
        P&L source is wired (Phase 4/5).
        """
        from core.risk import Portfolio, cluster_of, load_clusters
        book = Portfolio()
        clusters = load_clusters()
        for sym, pos in list(cls.user_positions.items()):
            if not isinstance(pos, dict):
                continue
            try:
                qty = float(pos.get("qty") or pos.get("entry_qty") or 0)
                entry = float(pos.get("entry_price") or pos.get("entry") or 0)
                stop = float(pos.get("stoploss") or pos.get("stop") or 0)
            except (TypeError, ValueError):
                continue
            if qty > 0 and entry > 0 and stop > 0:
                book.add_open(sym, cluster_of(sym, clusters), qty * abs(entry - stop))
        return book

    @classmethod
    def _attach_sizing(cls, gatekeeper_res, symbol, math_setup, regime_meta, payload):
        """Size an authorised proposal and stamp qty / risk / rejection onto
        the UI card (improved §4.3)."""
        try:
            from core.risk import size, load_risk_limits
            from core.types import Proposal
            geo = math_setup.get("execution_geometry") or {}
            entry = float(geo.get("calculated_entry") or 0)
            stop = float(geo.get("padded_stop") or 0)
            target = float(geo.get("calculated_target") or 0)
            if entry <= 0 or stop <= 0:
                gatekeeper_res["Qty"] = 0
                gatekeeper_res["Risk_Rejection"] = "NO_GEOMETRY"
                return
            prop = Proposal(
                symbol=symbol, bias=math_setup.get("directional_bias", "LONG"),
                entry=entry, stop=stop, target=target,
                composite=float(math_setup.get("composite_score") or 0.0),
                regime=regime_meta.get("current_regime", "UNKNOWN"))
            adv = float(payload.get("adv_shares") or 0.0)
            out = size(prop, cls._build_portfolio(), load_risk_limits(), adv)
            if getattr(out, "reason", None):
                gatekeeper_res["Qty"] = 0
                gatekeeper_res["Risk_Amount"] = 0.0
                gatekeeper_res["Risk_Rejection"] = out.reason
            else:
                gatekeeper_res["Qty"] = out.qty
                gatekeeper_res["Risk_Amount"] = round(out.risk_amount, 2)
                gatekeeper_res["Risk_Rejection"] = None
        except Exception as e:
            logger.error(f"L3 sizing failed for {symbol}: {e}")
            gatekeeper_res["Qty"] = 0
            gatekeeper_res["Risk_Rejection"] = "SIZING_ERROR"

    @classmethod
    async def start_global_gatekeeper_loop(cls):
        logger.info("Starting Global Gatekeeper Loop (runs every 10s)")
        from intraday_gatekeeper import IntradayGatekeeper
        import json
        from diagnostic_ui import TerminalDashboard
        
        from signal_ledger import SignalLedger
        asyncio.create_task(SignalLedger.start_outcome_resolver())

        from journal.feature_log import FeatureLog, FeatureRecord
        from paths import FEATURES_DIR
        feature_log = FeatureLog(FEATURES_DIR)
        cls._feature_log = feature_log

        async def _feature_flush():
            while True:
                await asyncio.sleep(60)
                await asyncio.to_thread(feature_log.flush)

        asyncio.create_task(_feature_flush())

        async def _arm_resolver():
            """Label both arms of every escalation once its horizon has
            elapsed, reusing the ledger's cross-process bar bridge (§4.5)."""
            from journal.arms import resolve_arms
            from signal_ledger import SignalLedger
            from diagnostic_ui import TerminalDashboard as _TD

            async def _fetch(sym):
                token = next((k for k in _TD.active_states if sym and sym in k), sym)
                return await SignalLedger._fetch_recent_bars(token)

            primary = SignalLedger._measure_at()[-1]
            while True:
                await asyncio.sleep(300)
                try:
                    await resolve_arms(cls._get_arm_journal(), _fetch,
                                       horizon_min=primary,
                                       cost_pct=SignalLedger.ROUND_TRIP_COST_PCT)
                except Exception as e:
                    logger.error(f"Arm resolver error: {e}")

        asyncio.create_task(_arm_resolver())

        while True:
            for symbol, payload in list(TerminalDashboard.active_states.items()):
                token = payload.get('token') or symbol
                sym = payload.get('symbol') or symbol
                
                # Skip invalid symbols or broad market indices from individual actionable analysis
                if not sym or "Nifty 50" in sym or "Nifty Bank" in sym:
                    continue
                    
                ltp = payload.get("ltp", 0.0)
                norm_sym = cls._normalize_symbol(sym)
                # Use canonical normalized key for position lookup
                current_pos = cls.user_positions.get(norm_sym)
                if current_pos is None:
                    current_pos = {}
                try:
                    # ---- NEW: Run Math Engine FIRST for every symbol ----
                    # This is the ONE authoritative decision loop -- the only
                    # call site permitted to advance regime/whipsaw state
                    # (A-4). Every other caller (the 2Hz display refresh in
                    # api_server.py, and the on-demand /api/reasoning/instant
                    # endpoint via analyze_stock's own build_structured_payload
                    # call) reads with the default advance_state=False.
                    structured = cls.build_structured_payload(sym, payload, current_pos,
                                                              advance_state=True)
                    
                    # ---- Pass structured payload to Gatekeeper V2 ----
                    gatekeeper_res = IntradayGatekeeper.evaluate(
                        structured_payload=structured,
                        raw_payload=payload,
                        user_context={"position": current_pos} if current_pos else {},
                        ltp=ltp
                    )
                    
                    # ---- Log a feature vector for THIS symbol/tick, whether
                    # it gets proposed, rejected, or gated — before the
                    # debounce `continue` below, so a debounced-out tick is
                    # still recorded (Task 1.4). ----
                    math_setup_fl = structured.get("math_setup", {}) or {}
                    regime_meta_fl = structured.get("market_regime", {}) or {}
                    feature_log.write(FeatureRecord(
                        ts=int(time.time()),
                        symbol=norm_sym,
                        config_version=getattr(cls, "_config_version", 0),
                        features=_numeric_features(payload),
                        staleness={"microstructure": float(payload.get("data_age_s", 0.0))},
                        regime=regime_meta_fl.get("current_regime", "UNKNOWN"),
                        session_phase=regime_meta_fl.get("session_phase", "UNKNOWN"),
                        composite=math_setup_fl.get("composite_score"),
                        decision=_classify_decision(math_setup_fl, gatekeeper_res),
                    ))

                    # ---- L3 risk layer: every authorised proposal gets a
                    # quantity and a rupee risk, or a risk rejection (A-4.3). ----
                    if gatekeeper_res.get("llm_authorized"):
                        cls._attach_sizing(gatekeeper_res, sym, math_setup_fl,
                                           regime_meta_fl, payload)

                    has_pos = bool(current_pos)
                    current_advice = {
                        "action": gatekeeper_res.get("Action", ""),
                        "auth": gatekeeper_res.get("llm_authorized", False),
                        "has_pos": has_pos
                    }
                    
                    # --- DEBOUNCE LOGIC ---
                    debounce_record = cls.advice_debounce.setdefault(norm_sym, {"advice": current_advice, "count": 0})
                    if debounce_record["advice"] == current_advice:
                        debounce_record["count"] += 1
                    else:
                        cls.advice_debounce[norm_sym] = {"advice": current_advice, "count": 1}
                        
                    # Require 3 consecutive ticks of stability to accept state change
                    if cls.advice_debounce[norm_sym]["count"] < 3:
                        continue
                    # ----------------------
                    
                    last_advice = cls.last_math_advice.get(norm_sym)
                    
                    if current_advice != last_advice:
                        cls.last_math_advice[norm_sym] = current_advice
                        
                        if gatekeeper_res["llm_authorized"]:
                            if cls.llm_enabled.get(norm_sym):
                                # Authorized AND toggled ON -> run LLM!
                                
                                # Publish the pending state to UI immediately
                                gatekeeper_res["Status_Tag"] = "PENDING_LLM"
                                gatekeeper_res["Generated_Time"] = payload.get("current_time", "UNKNOWN")
                                cls.latest_reports[norm_sym] = json.dumps(gatekeeper_res, indent=2)
                                
                                # Avoid parallel duplicate tasks for the same symbol
                                if norm_sym not in cls.active_loops:
                                    cls.active_loops[norm_sym] = True
                                    
                                    # ---- FIX: Capture by value ----
                                    async def run_and_unlock(s=norm_sym, p=current_pos, sp=structured):
                                        try:
                                            await cls.analyze_stock(
                                                s, "gemini-2.5-flash", "", 
                                                user_position=p, 
                                                is_autonomous=True,
                                                precomputed_payload=sp
                                            )
                                        finally:
                                            cls.active_loops.pop(s, None)
                                                
                                    asyncio.create_task(run_and_unlock())
                            else:
                                # Authorized BUT toggled OFF -> add Tag and show in UI
                                gatekeeper_res["Status_Tag"] = "REQUIRED LLM ANALYZE"
                                gatekeeper_res["Reason"] = "Local Gatekeeper authorized LLM, but toggle is OFF."
                                gatekeeper_res["Generated_Time"] = payload.get("current_time", "UNKNOWN")
                                cls.latest_reports[norm_sym] = json.dumps(gatekeeper_res, indent=2)
                        else:
                            # Local gatekeeper Action
                            gatekeeper_res["Reason"] = gatekeeper_res.get("math_rejection", "Local Gatekeeper active. LLM analysis suppressed.")
                            gatekeeper_res["Generated_Time"] = payload.get("current_time", "UNKNOWN")
                            cls.latest_reports[norm_sym] = json.dumps(gatekeeper_res, indent=2)
                    else:
                        # State unchanged. Preserve UI card, do nothing.
                        pass
                        
                except Exception as e:
                    logger.error(f"Gatekeeper error for {sym}: {e}")
                    
            await asyncio.sleep(10)

    @classmethod
    def set_llm_toggle(cls, symbol: str, enabled: bool, user_position: dict = None):
        norm = cls._normalize_symbol(symbol)
        cls.llm_enabled[norm] = enabled

        if user_position is not None:
            cls.user_positions[norm] = user_position

    @classmethod
    async def stop_analysis_loop(cls, symbol: str):
        norm = cls._normalize_symbol(symbol)
        loop_data = cls.active_loops.get(norm)
        if loop_data:
            task = loop_data.get("task") if isinstance(loop_data, dict) else loop_data
            if task and not task.done():
                task.cancel()
                logger.info(f"Stopped background reasoning loop for {norm}")
        
        if norm in cls.active_loops:
            del cls.active_loops[norm]
        return True

    @classmethod
    async def generate_intraday_playbook(cls, model_name: str = "gemini-2.5-flash"):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return {"error": "GEMINI_API_KEY is missing."}
            
        try:
            logger.info("Initializing Market-Wide Discovery Pipeline...")
            from data_services.upstox_feed import UpstoxAuthenticator
            from upstox_client import Configuration, ApiClient
            from screener_engine import PreMarketScreener
            
            # Authenticate with Upstox for historical data
            auth = UpstoxAuthenticator()
            access_token = await auth.get_valid_token()
            if not access_token:
                return {"error": "Failed to get Upstox access token for Discovery Engine."}
                
            configuration = Configuration()
            configuration.access_token = access_token
            upstox_client = ApiClient(configuration)
            
            # 1. Run Screener 0 across Market
            screener = PreMarketScreener(upstox_client)
            top_picks = await screener.run_scan()
            
            if not top_picks:
                return {"error": "Screener 0 returned no candidates. Cannot generate playbook."}
                
            logger.info(f"Screener 0 returned {len(top_picks)} candidates. Compiling data payload...")
            
            from derivatives_engine import OptionsAnalyzer
            enriched_picks = []
            active_states = getattr(TerminalDashboard, "active_states", {})
            for pick in top_picks:
                token = pick["token"]
                state = active_states.get(token, {})
                pick["microstructure_available"] = token in active_states
                pick["obi"] = state.get("obi", "N/A")
                pick["cvd"] = state.get("cvd", "N/A")
                enriched_picks.append(pick)
            
            # 2. Compile the massive cross-section payload
            payload = {
                "global_market_context": getattr(TerminalDashboard, "global_market_context", None),
                "screener_candidates": enriched_picks
            }
            
            user_payload = json.dumps(payload, indent=2)
            
            system_prompt = (
                "You are an elite Quantitative Systems Architect acting as a Market-Wide Discoverer. "
                "Analyze this multi-factor matrix containing the Top 20 mathematically scored 'Screener 0' candidates, "
                "along with their technical metrics (OBI, CVD, structural levels), deep news summaries, and the current global macroeconomic context.\n"
                "The candidates have already been mathematically scored (magnitude_score) and assigned a directional bias (LONG/SHORT) by the Math Engine.\n"
                "Your objective is to generate an actionable Discovery Playbook for the current session. "
                "You MUST act as a narrative synthesizer. Do NOT override the provided 'directional_bias' of a candidate. "
                "Instead, explain WHY the math engine selected this bias by correlating the news and technical metrics.\n"
                "The output MUST be a strict JSON object with the following schema:\n"
                "{\n"
                "  \"macro_weather\": \"Your assessment of the global market bias based on the macro context.\",\n"
                "  \"watchlist\": [\n"
                "    {\n"
                "      \"symbol\": \"STOCK_SYMBOL\",\n"
                "      \"rationale\": \"Precise reason this stock was selected, correlating its technical score and setup with the macro/thematic news.\",\n"
                "      \"strategy\": \"Execution strategy (e.g., 'Buy only if price holds above VWAP')\",\n"
                "      \"entry\": 1425.50, // MUST BE A FLOAT (e.g., derived from Camarilla H3 or prev_day_high)\n"
                "      \"target\": 1472.00, // MUST BE A FLOAT\n"
                "      \"stoploss\": 1398.00, // MUST BE A FLOAT\n"
                "      \"confidence\": \"High/Medium/Low\",\n"
                "      \"risk\": \"High/Medium/Low\",\n"
                "      \"token\": \"INSTRUMENT_TOKEN\",\n"
                "      \"exchange\": \"EXCHANGE_NAME\"\n"
                "    }\n"
                "  ]\n"
                "}\n"
                "Select EXACTLY the Top 10 High-Conviction Stocks from the provided Top 20 candidates. "
                "Ensure you include the 'token' and 'exchange' fields exactly as provided in the candidates list so the UI can hot-load them. "
                "Output ONLY valid JSON, do not include markdown code block wrappers (like ```json)."
            )
            
            full_prompt = f"{system_prompt}\n\nDATA PAYLOAD:\n{user_payload}"
            
            logger.info("Triggering LLM for Deep Discovery Playbook generation...")
            client = genai.Client(api_key=api_key)
            response = await client.aio.models.generate_content(
                model=model_name,
                contents=full_prompt
            )
            
            # 3. Parse response and update state
            text = response.text.strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.endswith("```"):
                text = text[:-3]
                
            playbook_json = json.loads(text)
            playbook_json["generated_at"] = datetime.datetime.now().isoformat()
            playbook_json["session_date"] = datetime.datetime.now().strftime("%Y-%m-%d")
            
            TerminalDashboard.dashboard_intraday_plays = playbook_json
            
            try:
                from paths import PLAYBOOK_PATH
                with open(PLAYBOOK_PATH, "w") as f:
                    json.dump(playbook_json, f, indent=2)
            except Exception as e:
                logger.error(f"Failed to save playbook to disk: {e}")
            
            logger.info("Successfully generated Discovery Playbook.")
            return playbook_json
            
        except Exception as e:
            logger.error(f"Error generating intraday playbook: {e}")
            return {"error": str(e)}
