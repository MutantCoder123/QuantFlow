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
    # Throttle concurrent API calls to avoid rate limit bans
    llm_semaphore = asyncio.Semaphore(3)
    
    # Store background tasks for toggling
    active_loops = {}
    
    # Store the latest generated reports
    latest_reports = {}
    
    # Phase 9: Global Alerting Matrix
    global_alerts = []
    alert_counter = 0
    
    @classmethod
    def _get_token_for_symbol(cls, symbol: str):
        from config import load_watchlist_from_csv
        # Ideally we don't load this every time, but it's small enough.
        watchlist = load_watchlist_from_csv()
        for token, meta in watchlist.items():
            if meta.get("symbol", "") == symbol or meta.get("symbol", "").split('-')[0] == symbol:
                return token
        return None

    @classmethod
    async def analyze_stock(cls, symbol: str, model_name: str, system_prompt: str, user_position: dict = None):
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
            
        # Deep copy payload so we don't pollute live data
        import copy
        payload_copy = copy.deepcopy(payload)
        if user_position:
            payload_copy["user_position"] = user_position


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
                # (Assuming the system_prompt contains the overarching instructions)
                full_prompt = f"SYSTEM INSTRUCTION:\n{system_prompt}\n\nDATA PAYLOAD:\n{user_payload}"
                
                response = await client.aio.models.generate_content(
                    model=model_name,
                    contents=full_prompt
                )
                
                report_text = response.text
                cls.latest_reports[symbol] = report_text
                
                # Phase 9: Parse and Trigger Alerts
                verdict_match = re.search(r"VERDICT:\s*(.*)", report_text, re.IGNORECASE)
                score_match = re.search(r"CONFIDENCE SCORE:\s*(\d+)", report_text, re.IGNORECASE)
                
                if verdict_match and score_match:
                    verdict = verdict_match.group(1).strip()
                    score = int(score_match.group(1))
                    
                    actionable_keywords = ["Strong Buy", "Strong Sell", "Scale Out"]
                    if score >= 8 and any(kw.lower() in verdict.lower() for kw in actionable_keywords):
                        cls.alert_counter += 1
                        # Insert at front so newest is always first
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
                
                logger.info(f"Successfully generated reasoning report for {symbol}.")
                return report_text
                
            except Exception as e:
                logger.error(f"Reasoning Engine API Exception for {symbol}: {e}")
                error_msg = f"Error generating report: {str(e)}"
                cls.latest_reports[symbol] = error_msg
                return error_msg

    @classmethod
    async def _analysis_loop(cls, symbol: str, interval: int, model_name: str, system_prompt: str, user_position: dict = None):
        logger.info(f"Starting background reasoning loop for {symbol} every {interval}s")
        while True:
            await cls.analyze_stock(symbol, model_name, system_prompt, user_position=user_position)
            await asyncio.sleep(interval)

    @classmethod
    async def start_analysis_loop(cls, symbol: str, interval: int, model_name: str, system_prompt: str, user_position: dict = None):
        # Stop existing loop if one is already running for this symbol
        await cls.stop_analysis_loop(symbol)
        
        task = asyncio.create_task(cls._analysis_loop(symbol, interval, model_name, system_prompt, user_position=user_position))
        cls.active_loops[symbol] = task
        return True

    @classmethod
    async def stop_analysis_loop(cls, symbol: str):
        task = cls.active_loops.get(symbol)
        if task and not task.done():
            task.cancel()
            logger.info(f"Stopped background reasoning loop for {symbol}")
        
        if symbol in cls.active_loops:
            del cls.active_loops[symbol]
        return True
