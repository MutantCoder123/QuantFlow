import re

# 1. Update reasoning_engine.py
with open('trading_copilot/reasoning_engine.py', 'r', encoding='utf-8') as f:
    content = f.read()

new_methods = """
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
                                asyncio.create_task(cls.analyze_stock(sym, "gemini-2.5-flash", "", user_position=current_pos))
                        else:
                            # Authorized BUT toggled OFF -> add Tag and show in UI
                            gatekeeper_res["Status_Tag"] = "REQUIRED LLM ANALYZE"
                            report_text = f"### 🎯 ACTIONABLE ADVICE\\n- VERDICT: {gatekeeper_res['Action']}\\n- CONFIDENCE SCORE: {gatekeeper_res['Confidence_Score']}\\n- POSITION MANAGEMENT: Local Gatekeeper authorized LLM, but toggle is OFF.\\n- ACTION:\\n```json\\n{json.dumps(gatekeeper_res, indent=2)}\\n```"
                            cls.latest_reports[sym] = report_text
                    else:
                        # Local gatekeeper Action
                        report_text = f"### 🎯 ACTIONABLE ADVICE\\n- VERDICT: {gatekeeper_res['Action']}\\n- CONFIDENCE SCORE: {gatekeeper_res['Confidence_Score']}\\n- POSITION MANAGEMENT: Local Gatekeeper active. LLM analysis suppressed to save tokens.\\n- ACTION:\\n```json\\n{json.dumps(gatekeeper_res, indent=2)}\\n```"
                        cls.latest_reports[sym] = report_text
                        
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
"""

content = re.sub(
    r'    @classmethod\s+async def _analysis_loop\(cls.*?return True',
    new_methods.strip(),
    content,
    flags=re.DOTALL
)

with open('trading_copilot/reasoning_engine.py', 'w', encoding='utf-8') as f:
    f.write(content)

# 2. Update api_server.py
with open('trading_copilot/api_server.py', 'r', encoding='utf-8') as f:
    api = f.read()

api = re.sub(
    r'    await ReasoningEngine.start_analysis_loop\(req.symbol, req.interval, req.model, req.prompt, req.user_position, req.user_intent\)',
    r'    ReasoningEngine.set_llm_toggle(req.symbol, True, req.user_position)',
    api
)

api = re.sub(
    r'    await ReasoningEngine.stop_analysis_loop\(req.symbol\)',
    r'    ReasoningEngine.set_llm_toggle(req.symbol, False)',
    api
)

if 'asyncio.create_task(ReasoningEngine.start_global_gatekeeper_loop())' not in api:
    api = api.replace(
        'asyncio.create_task(poll_news())',
        'asyncio.create_task(poll_news())\\n    asyncio.create_task(ReasoningEngine.start_global_gatekeeper_loop())'
    )

with open('trading_copilot/api_server.py', 'w', encoding='utf-8') as f:
    f.write(api)

print('Patched successfully!')
