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
    def build_structured_payload(cls, symbol: str, payload: dict, user_position: dict = None, user_intent: dict = None) -> dict:
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
        regime_metadata = manager.determine_regime(tactical_payload)
        tactical_payload["market_regime"] = regime_metadata
        
        # Inject Conviction Score & Math Setup
        from conviction_scorer import ConvictionScorerRegistry
        scorer = ConvictionScorerRegistry.get_or_create(symbol)
        math_setup = scorer.score_setup(tactical_payload, payload)
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
                    "and `math_setup.expectancy_matrix` (implied_probability, statistical_edge) as ground truth. "
                    "Do not recalculate. Only modify if a catastrophic qualitative event demands it, "
                    "and only within the ADJUSTMENT CONSTRAINTS below.\n\n"
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
                    stat_edge = expectancy_matrix.get("statistical_edge", 0.0)
                    
                    calc_priority = min(10, int(abs(composite_score) * 20))
                    calc_confidence = min(10, int(stat_edge * 33)) if stat_edge > 0 else 0
                    
                    risk_params = ticket.get("risk_parameters") or {}
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

    @classmethod
    async def start_global_gatekeeper_loop(cls):
        logger.info("Starting Global Gatekeeper Loop (runs every 10s)")
        from intraday_gatekeeper import IntradayGatekeeper
        import json
        from diagnostic_ui import TerminalDashboard
        
        from signal_ledger import SignalLedger
        asyncio.create_task(SignalLedger.start_outcome_resolver())
        
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
                    structured = cls.build_structured_payload(sym, payload, current_pos)
                    
                    # ---- Pass structured payload to Gatekeeper V2 ----
                    gatekeeper_res = IntradayGatekeeper.evaluate(
                        structured_payload=structured,
                        raw_payload=payload,
                        user_context={"position": current_pos} if current_pos else {},
                        ltp=ltp
                    )
                    
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
                playbook_path = os.path.join("trading_copilot", "playbook_state.json")
                with open(playbook_path, "w") as f:
                    json.dump(playbook_json, f, indent=2)
            except Exception as e:
                logger.error(f"Failed to save playbook to disk: {e}")
            
            logger.info("Successfully generated Discovery Playbook.")
            return playbook_json
            
        except Exception as e:
            logger.error(f"Error generating intraday playbook: {e}")
            return {"error": str(e)}
