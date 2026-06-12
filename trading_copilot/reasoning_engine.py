import os
import asyncio
import json
import logging
import re
import time
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
    async def analyze_stock(cls, symbol: str, model_name: str, system_prompt: str, user_position: dict = None, user_intent: dict = None):
        token = cls._get_token_for_symbol(symbol)
        if not token:
            error_msg = f"Error: Cannot find active data stream for {symbol}."
            cls.latest_reports[symbol] = error_msg
            return error_msg
            
        # Extract live JSON telemetry from memory
        payload = TerminalDashboard.active_states.get(token)
        if not payload:
            error_msg = f"Error: No live telemetry data available yet for {symbol}."
            cls.latest_reports[symbol] = error_msg
            return error_msg
            
        from mtf_extractor import MTFFeatureExtractor, sanitize_for_json
        import pandas as pd
        import numpy as np

    @classmethod
    def build_structured_payload(cls, symbol: str, payload: dict, user_position: dict = None, user_intent: dict = None) -> dict:
        from mtf_extractor import MTFFeatureExtractor, sanitize_for_json
        import pandas as pd
        import numpy as np
        import os, json, time
        
        if user_position is None:
            user_position = cls.user_positions.get(symbol)
        
        
        def _load_macro_baselines() -> dict:
            try:
                baselines_path = os.path.join(os.path.dirname(__file__), 'data', 'macro_baselines.json')
                if os.path.exists(baselines_path):
                    with open(baselines_path, 'r') as f:
                        return json.load(f)
            except Exception as e:
                logger.error(f"Error loading macro_baselines.json: {e}")
            return {}

        baselines = _load_macro_baselines()
        stock_macro = baselines.get(symbol, {})
        
        volatility_edge_52w = stock_macro.get("volatility_edge_52w", {})
        options_positioning_52w = stock_macro.get("options_positioning_52w", {})
        structural_liquidity_5y = stock_macro.get("structural_liquidity_5y", {})
        regime_confluence_5y = stock_macro.get("regime_confluence_5y", {})

        ltp = payload.get("ltp", 0.0)
        
        # Calculate dynamic edge metrics
        atm_iv_live = payload.get("atm_iv")
        hv_20d = volatility_edge_52w.get("historical_vol_20d")
        iv_hv_premium_pct = (atm_iv_live - hv_20d) if (atm_iv_live is not None and hv_20d is not None) else None
        
        max_pain_price = payload.get("max_pain_price")
        max_pain_divergence_pct = ((ltp - max_pain_price) / max_pain_price * 100) if (ltp and max_pain_price) else None
        
        poc_price = structural_liquidity_5y.get("volume_poc_price")
        distance_to_poc_pct = ((ltp - poc_price) / poc_price * 100) if (ltp and poc_price) else None
        
        # Calculate Flow Regime
        cvd = payload.get("cvd", 0.0)
        obi = payload.get("obi", 0.0)
        if cvd > 0 and obi > 0.05:
            flow_regime = "AGGRESSIVE_BUYING"
        elif cvd < 0 and obi < -0.05:
            flow_regime = "AGGRESSIVE_SELLING"
        elif cvd > 0 and obi < -0.05:
            flow_regime = "ABSORPTION_SELLING"
        elif cvd < 0 and obi > 0.05:
            flow_regime = "ABSORPTION_BUYING"
        else:
            flow_regime = "NEUTRAL_FLOW"
            
        # Calculate Volatility Regime & IVR
        iv_pct_52w = volatility_edge_52w.get("iv_percentile_52w")
        if iv_pct_52w is not None:
            if iv_pct_52w > 80:
                vol_regime = "VOLATILITY_EXPANSION"
            elif iv_pct_52w < 20:
                vol_regime = "VOLATILITY_COMPRESSION_SQUEEZE"
            else:
                vol_regime = "NORMAL_VOLATILITY"
        else:
            vol_regime = None
            
        iv_52w_high = volatility_edge_52w.get("iv_52w_high")
        iv_52w_low = volatility_edge_52w.get("iv_52w_low")
        if payload.get("ivr") is not None:
            ivr_live = payload.get("ivr")
        elif atm_iv_live is not None and iv_52w_high is not None and iv_52w_low is not None and iv_52w_high > iv_52w_low:
            ivr_live = ((atm_iv_live - iv_52w_low) / (iv_52w_high - iv_52w_low)) * 100
        else:
            ivr_live = None

        # Load Strike Migration
        drift_20d = options_positioning_52w.get("drift_20d_strike_migration")

        # Fetch Catalyst Engine explicitly from Memory Cache
        catalyst_cache = getattr(TerminalDashboard, "catalyst_cache", {})
        latest_catalyst = payload.get("latest_catalyst") or catalyst_cache.get(symbol, {})

        import datetime
        now = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
        market_state = "LIVE"
        if now.time() < datetime.time(9, 15) or now.time() > datetime.time(15, 30):
            market_state = "CLOSED"
            
        session_vwap = payload.get("session_vwap", 0.0)
        price_to_vwap_pct = payload.get("price_to_vwap_pct", 0.0)
        whale_cvd_live = payload.get("whale_cvd_live", 0)
        whale_cvd_ema_1h = payload.get("whale_cvd_ema_1h", 0.0)
        whale_cvd_slope = payload.get("whale_cvd_slope", 0.0)
        vol_z_score_5m = payload.get("vol_z_score_5m", 0.0)
        
        if market_state == "CLOSED":
            whale_cvd_live = 0
            vol_z_score_5m = 0.0
            
        if market_state == "CLOSED":
            divergence_state = "MARKET_CLOSED_SUPPRESSED"
        elif price_to_vwap_pct < -0.1 and whale_cvd_slope > 0:
            divergence_state = "HIDDEN_BULLISH_ABSORPTION"
        elif price_to_vwap_pct > 0.1 and whale_cvd_slope < 0:
            divergence_state = "HIDDEN_BEARISH_DISTRIBUTION"
        elif (price_to_vwap_pct > 0.1 and whale_cvd_slope > 0) or (price_to_vwap_pct < -0.1 and whale_cvd_slope < 0):
            divergence_state = "MOMENTUM_CONFIRMED"
        else:
            divergence_state = "EQUILIBRIUM_CHOP"

        current_time_str = now.strftime("%I:%M %p").lower()

        # Build strict hierarchical dictionary
        tactical_payload = {
            "market_state": market_state,
            "symbol": symbol,
            "timestamp": payload.get("timestamp", int(time.time() * 1000)),
            "current_time": current_time_str,
            "ltp": ltp,
            "prev_close": payload.get("prev_close", 0.0),
            "high_probability_setup": payload.get("high_probability_setup", False),
            "user_context": {},
            "1_live_microstructure": {
                "order_flow": {
                    "cvd": cvd,
                    "obi": obi,
                    "vol_z_score_5m": vol_z_score_5m,
                    "flow_regime": flow_regime,
                    "session_vwap": session_vwap,
                    "price_to_vwap_pct": price_to_vwap_pct,
                    "whale_cvd_live": whale_cvd_live,
                    "whale_cvd_ema_1h": whale_cvd_ema_1h
                },
                "mtf_technicals": {
                    **MTFFeatureExtractor.extract_all(payload, ltp),
                    "kinetic_divergence": {
                        "whale_cvd_slope": whale_cvd_slope,
                        "divergence_state": divergence_state
                    }
                }
            },
            "2_derivatives_matrix_52w": {
                "volatility_edge": {
                    "ivr_live": ivr_live,
                    "iv_percentile_52w": iv_pct_52w,
                    "iv_hv_premium_pct": iv_hv_premium_pct,
                    "regime": vol_regime
                },
                "options_positioning": {
                    "stock_pcr": payload.get("stock_pcr", 1.0),
                    "pcr_percentile_52w": options_positioning_52w.get("pcr_percentile_52w"),
                    "oi_volume_shock_52w_z": options_positioning_52w.get("oi_volume_shock_52w_z"),
                    "max_pain_price": max_pain_price,
                    "max_pain_divergence_pct": max_pain_divergence_pct,
                    "drift_20d_strike_migration": drift_20d
                }
            },
            "3_macro_statistical_edge_5y": {
                "structural_liquidity": {
                    "volume_poc_price": poc_price,
                    "value_area_high": structural_liquidity_5y.get("value_area_high"),
                    "value_area_low": structural_liquidity_5y.get("value_area_low"),
                    "distance_to_poc_pct": distance_to_poc_pct
                },
                "regime_confluence": {
                    "alpha_vs_nifty_5y": regime_confluence_5y.get("alpha_5y"),
                    "beta_vs_nifty_5y": regime_confluence_5y.get("beta_5y"),
                    "macro_trend_alignment": regime_confluence_5y.get("macro_trend_alignment")
                }
            },
            "4_catalyst_engine": {
                "raw_news": latest_catalyst.get("raw_news", [])
            }
        }
        
        # Inject macro market context for global awareness (Phase 14.7)
        if hasattr(TerminalDashboard, "global_market_context") and TerminalDashboard.global_market_context:
            tactical_payload["global_market_context"] = TerminalDashboard.global_market_context
            
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
                
            tactical_payload["user_context"]["position"] = user_position
        if user_intent:
            tactical_payload["user_context"]["intent"] = user_intent

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
                
                # Format payload for the prompt
                user_payload = json.dumps(payload_copy, indent=2)
                
                # Combine system prompt and user payload.
                default_prompt = (
                    "ROLE & OPERATIONAL FRAMEWORK:\n"
                    "You are the Lead Quantitative Execution Strategist. You are an API endpoint that ingests a Phase 6 Hierarchical JSON Telemetry Payload and outputs raw, deterministic execution logic. Your goal is to synthesize 4 data blocks to isolate true institutional positioning from retail traps, optimizing for a 2-6 hour intraday predictive horizon.\n\n"
                    "CRITICAL CONSTRAINTS & NULL SAFETY:\n"
                    "1. ABSOLUTE DATA ADHERENCE: Never assume or extrapolate targets. If metrics are null, 0, or state 'MARKET_CLOSED_SUPPRESSED', default to a neutral risk mitigation posture.\n"
                    "2. MARKET STATE SHIELD: If root level `market_state` is 'CLOSED' or 'AUCTION', immediately output Action: 'Wait' or 'Hold' with a Priority_Score of 0. Do not calculate new trade targets on post-market ghost books.\n\n"
                    "STEP 1: THE 4-BLOCK MULTI-VARIABLE SYNTHESIS MATRIX\n"
                    "You must cross-examine the payload fields using the following strict institutional logic:\n\n"
                    "1. BLOCK 1: LIVE MICROSTRUCTURE & INTENT ANALYSIS\n"
                    "   - Evaluate `price_to_vwap_pct` against `kinetic_divergence.divergence_state`. If state is 'HIDDEN_BULLISH_ABSORPTION', price drops are artificial liquidity sweeps; you must heavily favor LONG or HOLD positions. If 'HIDDEN_BEARISH_DISTRIBUTION', favor SHORT or CLOSE.\n"
                    "   - Evaluate `mtf_technicals.elasticity_risk`. If it reads 'OVERSTRETCHED', you face an imminent mean-reversion snapback. You MUST penalize breakout continuation trades. Only authorize mean-reversion setups or 'Wait'.\n"
                    "   - Read `mtf_technicals.key_geometry`. If LTP is within 0.2% of a reversal neckline (e.g., `double_top`) on high volume (`vol_z_score_5m` > 2), anticipate a structural breakout or immediate rejection.\n\n"
                    "2. BLOCK 2: DERIVATIVES MATRIX (THE PRICING REALITY)\n"
                    "   - Analyze `volatility_edge.ivr_live` and `iv_percentile_52w`. High levels (>70) indicate massive premium expansion. Require an overwhelming structural edge to buy into expansion.\n"
                    "   - Synthesize `options_positioning.max_pain_divergence_pct`. If divergence is > 5% and expiration is approaching, apply a structural gravity factor dragging LTP toward `max_pain_price`.\n\n"
                    "3. BLOCK 3: MACRO STATISTICAL EDGE (THE CONCRETE WALLS)\n"
                    "   - Measure LTP against `structural_liquidity.volume_poc_price`. This is an absolute multi-year liquidity wall. Never short directly on top of a 5-year POC floor, and never long directly under a major Value Area High rejection.\n"
                    "   - Cross-check `regime_confluence.alpha_vs_nifty_5y`. If alpha is highly negative, the stock has persistent secular weakness. Short setups require less volume conviction than long setups.\n\n"
                    "4. BLOCK 4: CATALYST ENGINE\n"
                    "   - Parse the `raw_news` array. Map news sentiment directly against Block 1 order flow. If headlines are highly bullish but `whale_cvd_ema_1h` is flat/negative, classify the asset as an active Institutional Distribution Trap and avoid long entries.\n\n"
                    "STEP 2: DIRECTIONAL CONTEXT & TRADE ACTIONS\n"
                    "- If `user_context.position` is completely empty: You are hunting entries. Output Action as 'Long', 'Short', or 'Wait'.\n"
                    "- If `user_context.position` exists: You are managing risk. You are restricted to outputting 'Hold', 'Close', or 'Wait'. Evaluate position PnL using entry price vs LTP and match against local structural stops.\n\n"
                    "OUTPUT FORMAT:\n"
                    "Output NOTHING except a raw, valid JSON object that can be directly passed to `json.loads()`. Do not wrap the output in markdown blocks, backticks, or prepend text. Every numeric field must be a float or null, strings must be exact matches.\n\n"
                    "{\n"
                    "  \"Action\": \"Short/Long/Hold/Close/Wait\",\n"
                    "  \"Entry_Target_Price\": <float or null>,\n"
                    "  \"Stoploss\": <float or null>,\n"
                    "  \"Exit_Target_Price\": <float or null>,\n"
                    "  \"Confidence_Score\": <int from 1 to 10>,\n"
                    "  \"Risk_Percentage\": <float>,\n"
                    "  \"Priority_Score\": <int from 1 to 10>, // Set > 6 ONLY if a high-conviction asymmetric edge or critical position exit exists right now\n"
                    "  \"Reason\": \"<string>\" // CRITICAL: Exactly 1-2 dense sentences detailing the precise multi-block convergence (e.g., Whale Absorption vs. Macro Walls) that dictates this action.\n"
                    "}"
                )
                
                strict_prompt = default_prompt + "\n\nCRITICAL: You function purely as a machine-readable data serialization engine. No markdown blocks, no footnotes. Just pure JSON output."
                
                if prompt_override:
                    output_format = "OUTPUT FORMAT:\n" + strict_prompt.split("OUTPUT FORMAT:\n")[1]
                    system_prompt = prompt_override + "\n\n" + output_format
                    strict_prompt = system_prompt
                
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
                    verdict = data.get("Action", "Wait").strip()
                    score = int(data.get("Priority_Score", 0))
                    
                    actionable_keywords = ["Long", "Short", "Close"]
                    if score >= 7 and any(kw.lower() in verdict.lower() for kw in actionable_keywords):
                        cls.alert_counter += 1
                        cls.global_alerts.insert(0, {
                            "id": cls.alert_counter,
                            "timestamp": time.time(),
                            "symbol": symbol,
                            "verdict": verdict,
                            "score": score,
                            "read": False
                        })
                        # Cap at 50 alerts in history to prevent memory leak
                        if len(cls.global_alerts) > 50:
                            cls.global_alerts.pop()
                            
                        logger.warning(f"🚨 SYSTEM ALERT TRIGGERED for {symbol}: {verdict} (Score: {score})")
                        
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
