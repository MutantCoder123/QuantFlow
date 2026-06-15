import os
import asyncio
import json
import logging
import re
import time
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
            user_position = cls.user_positions.get(symbol)
            
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
    async def analyze_stock(cls, symbol: str, model_name: str = "gemini-2.5-flash", prompt_override: str = None, user_position: dict = None, user_intent: dict = None) -> str:
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
            cls.latest_reports[symbol] = msg
            return msg

        payload = TerminalDashboard.active_states[target_token]
        payload_copy = cls.build_structured_payload(symbol, payload, user_position, user_intent)

        if payload_copy.get("math_setup", {}).get("setup_rejected", True):
            # TOKEN SAVING FIREWALL: Do NOT call the LLM API.
            current_time = payload_copy.get('current_time', 'UNKNOWN')
            print(f"[{current_time}] SETUP REJECTED BY MATH ENGINE. LLM bypassed to save tokens.")
            import json
            rejection_msg = json.dumps(payload_copy.get("math_setup"))
            cls.latest_reports[symbol] = rejection_msg
            return rejection_msg

        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            error_msg = "Error: GEMINI_API_KEY is missing from the environment."
            cls.latest_reports[symbol] = error_msg
            return error_msg

        # We lock this exact API call so at most 3 execute at the same time globally
        async with cls.llm_semaphore:
            try:
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
                
                strict_prompt = default_prompt + "\n\nCRITICAL: You are a machine-readable API endpoint. Output ONLY the JSON object. No markdown blocks, no footnotes, no preamble."
                
                if prompt_override:
                    strict_prompt = prompt_override + "\n\n" + strict_prompt
                
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
                
                cls.latest_reports[symbol] = report_text
                
                # Phase 9: Parse and Trigger Alerts
                try:
                    data = json.loads(report_text)
                    ticket = data.get("execution_ticket", {})
                    verdict = ticket.get("verdict", "UNKNOWN")
                    action = ticket.get("action_directive", "UNKNOWN")
                    
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
                cls.latest_reports[symbol] = report_text
                return report_text
                
            except Exception as e:
                logger.error(f"Reasoning Engine API Exception for {symbol}: {e}")
                error_msg = f"Error generating report: {str(e)}"
                cls.latest_reports[symbol] = error_msg
                return error_msg

    llm_enabled = {} # symbol -> bool

    @classmethod
    async def start_global_gatekeeper_loop(cls):
        logger.info("Starting Global Gatekeeper Loop (runs every 10s)")
        from intraday_gatekeeper import IntradayGatekeeper
        import json
        from diagnostic_ui import TerminalDashboard
        
        while True:
            for symbol, payload in list(TerminalDashboard.active_states.items()):
                token = payload.get('token', symbol)
                sym = payload.get('symbol', symbol)
                
                ltp = payload.get("ltp", 0.0)
                current_pos = cls.user_positions.get(sym, {})
                
                try:
                    gatekeeper_res = IntradayGatekeeper.evaluate(payload, {"position": current_pos} if current_pos else {}, ltp)
                    
                    if gatekeeper_res["llm_authorized"]:
                        if cls.llm_enabled.get(sym):
                            # Authorized AND toggled ON -> run LLM!
                            # Avoid parallel duplicate tasks for the same symbol
                            if sym not in cls.active_loops:
                                cls.active_loops[sym] = True
                                
                                async def run_and_unlock():
                                    try:
                                        await cls.analyze_stock(sym, "gemini-2.5-flash", "", user_position=current_pos)
                                    finally:
                                        if sym in cls.active_loops:
                                            del cls.active_loops[sym]
                                            
                                asyncio.create_task(run_and_unlock())
                        else:
                            # Authorized BUT toggled OFF -> add Tag and show in UI
                            gatekeeper_res["Status_Tag"] = "REQUIRED LLM ANALYZE"
                            gatekeeper_res["Reason"] = "Local Gatekeeper authorized LLM, but toggle is OFF."
                            cls.latest_reports[sym] = json.dumps(gatekeeper_res, indent=2)
                    else:
                        # Local gatekeeper Action
                        gatekeeper_res["Reason"] = "Local Gatekeeper active. LLM analysis suppressed to save tokens."
                        cls.latest_reports[sym] = json.dumps(gatekeeper_res, indent=2)
                        
                except Exception as e:
                    logger.error(f"Gatekeeper error for {sym}: {e}")
                    
            await asyncio.sleep(10)

    @classmethod
    def set_llm_toggle(cls, symbol: str, enabled: bool, user_position: dict = None):
        cls.llm_enabled[symbol] = enabled
        if user_position is not None:
            cls.user_positions[symbol] = user_position
        if enabled:
            cls.active_loops[symbol] = True
        else:
            if symbol in cls.active_loops:
                del cls.active_loops[symbol]

    @classmethod
    async def stop_analysis_loop(cls, symbol: str):
        loop_data = cls.active_loops.get(symbol)
        if loop_data:
            task = loop_data.get("task") if isinstance(loop_data, dict) else loop_data
            if task and not task.done():
                task.cancel()
                logger.info(f"Stopped background reasoning loop for {symbol}")
        
        if symbol in cls.active_loops:
            del cls.active_loops[symbol]
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
                derivatives = OptionsAnalyzer.stock_derivatives_state.get(token, {})
                state = active_states.get(token, {})
                pick["pcr"] = derivatives.get("stock_pcr", "N/A")
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
                "along with their technical metrics (OBI, CVD, PCR, IVR), deep news summaries, and the current global macroeconomic context.\n"
                "Your objective is to generate an actionable Discovery Playbook for the current session. "
"You MUST actively identify and suggest BOTH Bullish (Long) and Bearish (Short) trade setups, prioritizing maximum profit potential regardless of market direction. "
                "The output MUST be a strict JSON object with the following schema:\n"
                "{\n"
                "  \"macro_weather\": \"Your assessment of the global market bias based on the macro context.\",\n"
                "  \"watchlist\": [\n"
                "    {\n"
                "      \"symbol\": \"STOCK_SYMBOL\",\n"
                "      \"rationale\": \"Precise reason this stock was selected, correlating its technical score and setup with the macro/thematic news.\",\n"
                "      \"strategy\": \"Execution strategy (e.g., 'Buy only if price holds above VWAP')\",\n"
                "      \"entry\": \"Suggested entry price or zone\",\n"
                "      \"target\": \"Take profit target\",\n"
                "      \"stoploss\": \"Stop loss level\",\n"
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
